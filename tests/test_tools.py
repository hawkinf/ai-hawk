"""Function calling: `tools` e `tool_calls` precisam atravessar o servidor.

Regressao encontrada em 2026-08-16 com o Hermes Agent: o campo `tools` nao
existia em ChatCompletionRequest, e o Pydantic o descartava em silencio. O
modelo recebia a conversa sem ferramenta alguma e respondia de acordo - o
gemma4 chegou a ALUCINAR o resultado que deveria vir da ferramenta ("24 graus
em Sao Paulo"), e o qwen3coder respondeu honestamente que nao tinha acesso a
ferramentas. Nenhum dos dois estava errado: os dois foram cegados no caminho.
"""

from __future__ import annotations

import json

from app.providers.base import ModelSpec
from app.providers.openai_compat import OpenAICompatProvider
from app.schemas import ChatCompletionRequest

FERRAMENTA = {
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "Clima de uma cidade",
        "parameters": {
            "type": "object",
            "properties": {"cidade": {"type": "string"}},
            "required": ["cidade"],
        },
    },
}

SPEC = ModelSpec(
    id="x/modelo",
    provider="x",
    upstream_id="modelo",
    tier="free",
    label="Modelo",
    context_window=8192,
    max_output_tokens=2048,
)


def _provider() -> OpenAICompatProvider:
    return OpenAICompatProvider("x", base_url="http://x/v1", api_key="k")


def test_request_aceita_tools_e_tool_choice():
    """O schema precisa reconhecer os campos - senao o Pydantic os joga fora."""
    req = ChatCompletionRequest(
        model="x/modelo",
        messages=[{"role": "user", "content": "Qual o clima?"}],
        tools=[FERRAMENTA],
        tool_choice="auto",
    )
    assert req.tools == [FERRAMENTA]
    assert req.tool_choice == "auto"


def test_payload_repassa_tools_ao_provedor():
    """O bug original: os campos morriam antes de virar corpo da requisicao."""
    req = ChatCompletionRequest(
        model="x/modelo",
        messages=[{"role": "user", "content": "Qual o clima?"}],
        tools=[FERRAMENTA],
        tool_choice="auto",
    )
    body = _provider()._payload(req, SPEC, stream=False)
    assert body["tools"] == [FERRAMENTA]
    assert body["tool_choice"] == "auto"


def test_payload_sem_tools_nao_inventa_o_campo():
    """Requisicao comum nao pode ganhar campos novos (regressao de formato)."""
    req = ChatCompletionRequest(
        model="x/modelo",
        messages=[{"role": "user", "content": "oi"}],
    )
    body = _provider()._payload(req, SPEC, stream=False)
    assert "tools" not in body
    assert "tool_choice" not in body
    assert body["messages"] == [{"role": "user", "content": "oi"}]


def test_ida_e_volta_da_conversa_com_ferramenta():
    """A conversa completa: assistente pede, `tool` responde, modelo conclui.

    Sem o papel "tool" e sem tool_calls/tool_call_id na mensagem, o cliente nao
    consegue devolver o RESULTADO da ferramenta e o laco de agente trava.
    """
    chamada = {
        "id": "call_1",
        "type": "function",
        "function": {"name": "get_weather", "arguments": '{"cidade":"Sao Paulo"}'},
    }
    req = ChatCompletionRequest(
        model="x/modelo",
        messages=[
            {"role": "user", "content": "Qual o clima?"},
            {"role": "assistant", "tool_calls": [chamada]},
            {"role": "tool", "tool_call_id": "call_1", "content": "24 graus"},
        ],
        tools=[FERRAMENTA],
    )
    body = _provider()._payload(req, SPEC, stream=False)
    msgs = body["messages"]
    assert msgs[1]["tool_calls"] == [chamada]
    assert msgs[2] == {"role": "tool", "tool_call_id": "call_1", "content": "24 graus"}
    # Campos vazios nao podem vazar para o upstream: alguns provedores recusam
    # "content": null ou chaves desconhecidas.
    assert all(v is not None for m in msgs for v in m.values())


def test_resposta_com_tool_calls_chega_ao_cliente(client, monkeypatch):
    """O provedor devolve tool_calls; a rota nao pode descartar."""
    from app.providers.base import ChatResult

    chamada = {
        "id": "call_9",
        "type": "function",
        "function": {"name": "get_weather", "arguments": '{"cidade":"Santos"}'},
    }

    async def chat_com_tool(self, req, spec):
        return ChatResult(text="", tool_calls=[chamada], finish_reason="tool_calls")

    # Remendar a classe importada de tests.conftest nao funciona: o pytest ja
    # carregou o conftest como outro modulo, entao seria outra classe. Pega-se
    # o provedor que o app REALMENTE usa.
    stub = client.app.state.registry.providers["stub"]
    monkeypatch.setattr(type(stub), "chat", chat_com_tool)

    r = client.post(
        "/v1/chat/completions",
        json={
            "model": "stub/eco-free",
            "messages": [{"role": "user", "content": "clima em Santos?"}],
            "tools": [FERRAMENTA],
        },
    )
    assert r.status_code == 200
    msg = r.json()["choices"][0]["message"]
    assert msg["tool_calls"] == [chamada]
    assert r.json()["choices"][0]["finish_reason"] == "tool_calls"
    # Com tool_calls o padrao OpenAI e content nulo - aqui, omitido.
    assert msg.get("content") is None


def test_resposta_comum_nao_ganha_tool_calls(client):
    """Anti-regressao: cliente antigo nao pode ver campo novo em resposta normal."""
    r = client.post(
        "/v1/chat/completions",
        json={"model": "stub/eco-free", "messages": [{"role": "user", "content": "oi"}]},
    )
    assert r.status_code == 200
    msg = r.json()["choices"][0]["message"]
    assert "tool_calls" not in msg
    assert msg["content"] == "eco: oi"


# --- streaming ---------------------------------------------------------------
# O gerador SSE so repassava deltas de TEXTO. Com ferramentas, o cliente recebia
# um fluxo vazio e desistia ("empty content after retries"). Foi o que travou o
# laco de agente do Hermes contra os modelos locais em 2026-08-16.


def test_stream_chunk_carrega_tool_calls():
    from app.schemas import stream_chunk

    chamada = {"index": 0, "id": "call_1", "function": {"name": "f", "arguments": "{}"}}
    c = stream_chunk("id1", "m", tool_calls=[chamada])
    assert c["choices"][0]["delta"]["tool_calls"] == [chamada]


def test_stream_chunk_sem_tool_calls_nao_inventa_a_chave():
    from app.schemas import stream_chunk

    c = stream_chunk("id1", "m", delta="oi")
    assert "tool_calls" not in c["choices"][0]["delta"]


def _sse_para_json(texto: str) -> list[dict]:
    """Extrai os objetos JSON de um corpo SSE, ignorando o [DONE]."""
    saida = []
    for linha in texto.splitlines():
        if not linha.startswith("data: "):
            continue
        corpo = linha[6:].strip()
        if corpo == "[DONE]":
            continue
        saida.append(json.loads(corpo))
    return saida


def test_streaming_repassa_tool_calls_e_finish_reason(client, monkeypatch):
    chamada = {
        "index": 0,
        "id": "call_7",
        "type": "function",
        "function": {"name": "get_weather", "arguments": '{"cidade":"Recife"}'},
    }

    async def stream_com_tool(self, req, spec):
        yield {"tool_calls": [chamada]}
        yield {"finish_reason": "tool_calls"}

    stub = client.app.state.registry.providers["stub"]
    monkeypatch.setattr(type(stub), "stream", stream_com_tool)

    r = client.post(
        "/v1/chat/completions",
        json={
            "model": "stub/eco-free",
            "messages": [{"role": "user", "content": "clima?"}],
            "stream": True,
            "tools": [FERRAMENTA],
        },
    )
    assert r.status_code == 200
    chunks = _sse_para_json(r.text)
    deltas = [c["choices"][0]["delta"] for c in chunks]
    assert any(d.get("tool_calls") == [chamada] for d in deltas)
    # O motivo tem que chegar como "tool_calls", nao o "stop" fixo de antes.
    assert chunks[-1]["choices"][0]["finish_reason"] == "tool_calls"


def test_streaming_de_texto_continua_igual(client):
    """Anti-regressao: o fluxo comum nao pode mudar de formato."""
    r = client.post(
        "/v1/chat/completions",
        json={
            "model": "stub/eco-free",
            "messages": [{"role": "user", "content": "oi"}],
            "stream": True,
        },
    )
    assert r.status_code == 200
    chunks = _sse_para_json(r.text)
    texto = "".join(c["choices"][0]["delta"].get("content") or "" for c in chunks)
    assert texto.strip() == "eco: oi"
    assert chunks[-1]["choices"][0]["finish_reason"] == "stop"
    assert all("tool_calls" not in c["choices"][0]["delta"] for c in chunks)
