"""Consumindo o ai-hawk a partir de um programa Python.

Duas formas:
  1. SDK oficial da OpenAI (pip install openai) - so trocar a base_url;
  2. httpx puro, sem dependencia de SDK.

Rode o ai-hawk antes:
    .venv\\Scripts\\python.exe -m uvicorn app.main:app --port 8080
"""

from __future__ import annotations

import json
import os

import httpx

BASE_URL = os.getenv("AI_HAWK_URL", "http://localhost:8080/v1")
API_KEY = os.getenv("AI_HAWK_KEY", "")  # vazio se HAWK_API_KEYS nao estiver setado


def headers() -> dict[str, str]:
    h = {"Content-Type": "application/json"}
    if API_KEY:
        h["Authorization"] = f"Bearer {API_KEY}"
    return h


# ---------------------------------------------------------------------------
# 1. Descobrir quais LLMs estao disponiveis
# ---------------------------------------------------------------------------


def listar_modelos() -> list[dict]:
    res = httpx.get(f"{BASE_URL}/models", headers=headers(), timeout=30)
    res.raise_for_status()
    return res.json()["data"]


# ---------------------------------------------------------------------------
# 2. Conversar - o campo "model" escolhe a LLM
# ---------------------------------------------------------------------------


def perguntar(modelo: str, pergunta: str, sistema: str | None = None) -> str:
    mensagens = []
    if sistema:
        mensagens.append({"role": "system", "content": sistema})
    mensagens.append({"role": "user", "content": pergunta})

    res = httpx.post(
        f"{BASE_URL}/chat/completions",
        headers=headers(),
        json={"model": modelo, "messages": mensagens},
        timeout=300,
    )
    if res.status_code == 402:
        raise RuntimeError(f"Guarda de custo: {res.json()['error']['message']}")
    res.raise_for_status()
    return res.json()["choices"][0]["message"]["content"]


# ---------------------------------------------------------------------------
# 3. Streaming - imprime conforme chega
# ---------------------------------------------------------------------------


def perguntar_streaming(modelo: str, pergunta: str) -> str:
    completo = ""
    with httpx.stream(
        "POST",
        f"{BASE_URL}/chat/completions",
        headers=headers(),
        json={
            "model": modelo,
            "messages": [{"role": "user", "content": pergunta}],
            "stream": True,
        },
        timeout=300,
    ) as res:
        res.raise_for_status()
        for linha in res.iter_lines():
            if not linha.startswith("data:"):
                continue
            dados = linha[5:].strip()
            if dados == "[DONE]":
                break
            chunk = json.loads(dados)
            if "error" in chunk:
                raise RuntimeError(chunk["error"]["message"])
            pedaco = (chunk["choices"][0].get("delta") or {}).get("content")
            if pedaco:
                print(pedaco, end="", flush=True)
                completo += pedaco
    print()
    return completo


# ---------------------------------------------------------------------------
# 4. Mesma coisa usando o SDK oficial da OpenAI
# ---------------------------------------------------------------------------


def via_sdk_openai(modelo: str, pergunta: str) -> str:
    from openai import OpenAI  # pip install openai

    client = OpenAI(base_url=BASE_URL, api_key=API_KEY or "sem-auth")
    resposta = client.chat.completions.create(
        model=modelo,
        messages=[{"role": "user", "content": pergunta}],
    )
    return resposta.choices[0].message.content


if __name__ == "__main__":
    modelos = listar_modelos()
    if not modelos:
        raise SystemExit(
            "Nenhum modelo disponivel. Suba o Ollama ou o scripts/mock_provider.py."
        )

    print("Modelos disponiveis:")
    for m in modelos:
        print(f"  {m['id']:<45} {m['tier']}")

    escolhido = modelos[0]["id"]
    print(f"\nUsando: {escolhido}\n")

    print("--- resposta completa ---")
    print(perguntar(escolhido, "O que e uma nota fiscal eletronica?"))

    print("\n--- resposta em streaming ---")
    perguntar_streaming(escolhido, "Liste 3 vantagens de um servidor de IA proprio.")
