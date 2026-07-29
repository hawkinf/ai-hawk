"""Testes da API compativel com OpenAI."""

from __future__ import annotations

import json

from tests.conftest import FREE_MODEL, PAID_MODEL

# --- servico ---------------------------------------------------------------


def test_health_reporta_guarda_de_custo(client):
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["allow_paid_models"] is False
    assert "stub" in body["providers"]


def test_ui_e_servida_na_raiz(client):
    res = client.get("/")
    assert res.status_code == 200
    assert "ai-hawk" in res.text


def test_ui_usa_caminhos_relativos(client):
    """A UI precisa funcionar sob prefixo de proxy reverso (ex.: /ai-hawk/).

    Caminho absoluto em asset quebra o deploy atras de proxy: o navegador
    pediria /static/... em vez de /ai-hawk/static/...
    """
    html = client.get("/").text
    assert 'href="static/style.css"' in html
    assert 'src="static/chat.js"' in html
    assert 'href="/static/' not in html
    assert 'src="/static/' not in html

    js = client.get("/static/chat.js").text
    assert "document.baseURI" in js
    assert 'fetch("/v1/' not in js


def test_ui_orienta_o_usuario_no_401(client):
    """Um 401 precisa dizer o que fazer, nao so mostrar o codigo HTTP.

    O corpo de um 401 pode vir do proxy em HTML (nao JSON), entao o
    tratamento tem que ser por status, nao por parse do corpo.
    """
    js = client.get("/static/chat.js").text
    assert "res.status === 401" in js
    assert "pedirChave" in js
    assert "Ajustes" in js
    # Precisa abrir os Ajustes e focar o campo, senao a chave fica escondida.
    assert "ajustes.open = true" in js
    assert "el.apikey.focus()" in js


# --- catalogo --------------------------------------------------------------


def test_models_lista_apenas_gratuitos_com_guarda_ativa(client):
    data = client.get("/v1/models").json()["data"]
    ids = [m["id"] for m in data]
    assert FREE_MODEL in ids
    assert PAID_MODEL not in ids
    assert all(m["tier"] == "free" for m in data)


def test_models_inclui_pagos_quando_liberado(paid_client):
    ids = [m["id"] for m in paid_client.get("/v1/models").json()["data"]]
    assert FREE_MODEL in ids
    assert PAID_MODEL in ids


# --- guarda de custo -------------------------------------------------------


def test_modelo_pago_bloqueado_com_402(client):
    """Modelo pago conhecido -> 402 explicito, nunca um 404 confuso."""
    res = client.post(
        "/v1/chat/completions",
        json={"model": PAID_MODEL, "messages": [{"role": "user", "content": "oi"}]},
    )
    assert res.status_code == 402
    assert res.json()["error"]["code"] == "paid_model_blocked"
    assert "ALLOW_PAID_MODELS" in res.json()["error"]["message"]


def test_modelo_pago_passa_quando_liberado(paid_client):
    res = paid_client.post(
        "/v1/chat/completions",
        json={"model": PAID_MODEL, "messages": [{"role": "user", "content": "oi"}]},
    )
    assert res.status_code == 200


# --- chat ------------------------------------------------------------------


def test_chat_nao_streaming(client):
    res = client.post(
        "/v1/chat/completions",
        json={"model": FREE_MODEL, "messages": [{"role": "user", "content": "ola"}]},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["object"] == "chat.completion"
    assert body["model"] == FREE_MODEL
    assert body["choices"][0]["message"]["content"] == "eco: ola"
    assert body["usage"]["total_tokens"] == 12


def test_chat_streaming_emite_chunks_e_done(client):
    with client.stream(
        "POST",
        "/v1/chat/completions",
        json={
            "model": FREE_MODEL,
            "messages": [{"role": "user", "content": "ola mundo"}],
            "stream": True,
        },
    ) as res:
        assert res.status_code == 200
        assert res.headers["content-type"].startswith("text/event-stream")
        raw = "".join(res.iter_text())

    linhas = [ln for ln in raw.split("\n\n") if ln.strip()]
    assert linhas[-1].strip() == "data: [DONE]"

    texto = ""
    for linha in linhas[:-1]:
        chunk = json.loads(linha.strip().removeprefix("data: "))
        assert chunk["object"] == "chat.completion.chunk"
        texto += chunk["choices"][0]["delta"].get("content") or ""
    assert texto.strip() == "eco: ola mundo"


def test_system_prompt_nao_quebra_a_conversa(client):
    res = client.post(
        "/v1/chat/completions",
        json={
            "model": FREE_MODEL,
            "messages": [
                {"role": "system", "content": "seja breve"},
                {"role": "user", "content": "teste"},
            ],
        },
    )
    assert res.json()["choices"][0]["message"]["content"] == "eco: teste"


# --- validacao -------------------------------------------------------------


def test_modelo_inexistente_retorna_404(client):
    res = client.post(
        "/v1/chat/completions",
        json={"model": "nao/existe", "messages": [{"role": "user", "content": "oi"}]},
    )
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "model_not_found"


def test_id_curto_resolve_para_id_completo(client):
    res = client.post(
        "/v1/chat/completions",
        json={"model": "eco-free", "messages": [{"role": "user", "content": "oi"}]},
    )
    assert res.status_code == 200
    assert res.json()["model"] == FREE_MODEL


def test_messages_vazio_retorna_422(client):
    res = client.post("/v1/chat/completions", json={"model": FREE_MODEL, "messages": []})
    assert res.status_code == 422


def test_somente_system_retorna_422(client):
    res = client.post(
        "/v1/chat/completions",
        json={"model": FREE_MODEL, "messages": [{"role": "system", "content": "x"}]},
    )
    assert res.status_code == 422


# --- autenticacao ----------------------------------------------------------


def test_sem_chave_retorna_401(auth_client):
    assert auth_client.get("/v1/models").status_code == 401


def test_chave_errada_retorna_401(auth_client):
    res = auth_client.get("/v1/models", headers={"Authorization": "Bearer errada"})
    assert res.status_code == 401


def test_chave_correta_passa(auth_client):
    res = auth_client.get("/v1/models", headers={"Authorization": "Bearer chave-secreta"})
    assert res.status_code == 200


def test_health_nao_exige_chave(auth_client):
    assert auth_client.get("/health").status_code == 200
