"""Provedor falso compativel com OpenAI, para desenvolvimento sem rede.

Serve para testar o ai-hawk (incluindo streaming e a interface web) quando
nenhum provedor real esta configurado. Nao usa rede externa e nao custa nada.

Uso:
    python scripts/mock_provider.py            # sobe em http://127.0.0.1:11434

Depois aponte o ai-hawk para ele no .env:
    OLLAMA_BASE_URL=http://127.0.0.1:11434/v1
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

app = FastAPI(title="mock-provider")

MODELS = ["eco-rapido", "eco-detalhado"]


class Message(BaseModel):
    role: str
    content: str


class Request(BaseModel):
    model: str
    messages: list[Message]
    stream: bool = False
    temperature: float | None = None
    max_tokens: int | None = None


def _reply(req: Request) -> str:
    ultima = next(
        (m.content for m in reversed(req.messages) if m.role == "user"), "(vazio)"
    )
    sistema = next((m.content for m in req.messages if m.role == "system"), None)
    partes = [
        f"Voce disse: **{ultima}**",
        "",
        f"Respondendo pelo modelo simulado `{req.model}`. "
        "Este provedor e local e nao consome creditos de nenhuma API.",
    ]
    if sistema:
        partes += ["", f"Instrucao de sistema recebida: _{sistema}_"]
    partes += [
        "",
        "```python",
        "def ola():",
        '    return "ai-hawk funcionando"',
        "```",
    ]
    return "\n".join(partes)


@app.get("/v1/models")
async def list_models() -> dict:
    created = int(time.time())
    return {
        "object": "list",
        "data": [
            {"id": m, "object": "model", "created": created, "owned_by": "mock"}
            for m in MODELS
        ],
    }


@app.post("/v1/chat/completions")
async def chat(req: Request):
    texto = _reply(req)
    completion_id = f"chatcmpl-{uuid.uuid4().hex[:16]}"

    if not req.stream:
        return {
            "id": completion_id,
            "object": "chat.completion",
            "created": int(time.time()),
            "model": req.model,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": texto},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 12,
                "completion_tokens": len(texto.split()),
                "total_tokens": 12 + len(texto.split()),
            },
        }

    async def gerar():
        for token in texto.split(" "):
            chunk = {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": req.model,
                "choices": [{"index": 0, "delta": {"content": token + " "}}],
            }
            yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
            await asyncio.sleep(0.03)
        yield "data: [DONE]\n\n"

    return StreamingResponse(gerar(), media_type="text/event-stream")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=11434, log_level="warning")
