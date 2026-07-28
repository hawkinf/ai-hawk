# ai-hawk

Servidor FastAPI que expõe uma API compatível com OpenAI sobre vários provedores
de LLM (Ollama, Groq, OpenRouter, Cerebras, Google, Anthropic, OpenAI), mais uma
interface de chat web.

## Regra número um

**Custo zero por padrão.** `ALLOW_PAID_MODELS=false` bloqueia todo modelo pago
com HTTP 402 *antes* de qualquer chamada de rede. Nunca contorne isso.

## Comandos

```bash
.venv\Scripts\python.exe -m uvicorn app.main:app --port 8080   # rodar
.venv\Scripts\python.exe -m pytest                             # testes (offline)
.venv\Scripts\python.exe -m ruff check .                       # lint
.venv\Scripts\python.exe scripts/mock_provider.py              # provedor falso p/ dev
```

## Mapa

| Arquivo | Papel |
|---|---|
| `app/main.py` | Rotas e tratamento de erro |
| `app/registry.py` | Provedores ativos, catálogo unificado, **guarda de custo** |
| `app/providers/openai_compat.py` | Adaptador único para todos os provedores dialeto-OpenAI |
| `app/providers/anthropic_p.py` | Adaptador nativo do Claude |
| `app/schemas.py` | Contratos da API (formato OpenAI) |
| `web/` | Interface de chat |

Detalhes em [.claude/AGENT_RULES.md](.claude/AGENT_RULES.md) e
[.claude/DEBUG_PLAYBOOK.md](.claude/DEBUG_PLAYBOOK.md).
