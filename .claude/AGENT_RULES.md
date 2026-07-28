# AGENT_RULES — ai-hawk

Regras específicas deste repositório. Complementam o `CLAUDE.md` global.

## Contexto

Servidor FastAPI que expõe uma API compatível com OpenAI sobre vários provedores
de LLM. Python 3.11+. Sem banco de dados. Estado só em memória.

## Invariantes (nunca quebrar)

1. **Guarda de custo.** Nenhuma chamada a provedor pago pode acontecer com
   `ALLOW_PAID_MODELS=false`. O bloqueio ocorre em `Registry.resolve()`, *antes*
   de qualquer I/O. Qualquer alteração ali exige teste cobrindo o 402.
2. **Compatibilidade OpenAI.** `/v1/models` e `/v1/chat/completions` seguem o
   formato da OpenAI. Campos extras são permitidos; remover ou renomear campos
   padrão quebra os clientes existentes.
3. **Nenhum segredo em log.** Chaves de API nunca vão para log, resposta de erro
   ou `/health`.
4. **Testes offline.** A suíte não pode fazer chamada de rede. Use o
   `StubProvider` de `tests/conftest.py`.

## Pipeline por tarefa

1. Diagnosticar — reproduzir com `scripts/mock_provider.py`, sem gastar API.
2. Corrigir — mudança mínima.
3. Validar — `ruff check .` com 0 erros e `pytest` verde.
4. Endurecer — todo erro de provedor vira `ProviderError` com status correto.
5. UX — mensagens de erro em português, acionáveis.
6. Commit.

## Adicionar um provedor novo

- **Fala o dialeto OpenAI?** Adicione uma entrada `OpenAICompatProvider` em
  `Registry._build`. Nenhum arquivo novo.
- **Protocolo próprio?** Novo módulo em `app/providers/`, implementando
  `ChatProvider`. Espelhe `anthropic_p.py`.
- Marque `tier="paid"` sempre que houver cobrança por token. Na dúvida, `paid`.

## Modelos Claude

Usar apenas ids do catálogo em `app/providers/anthropic_p.py`. A família 5
(`claude-opus-5`, `claude-sonnet-5`) **rejeita** `temperature`/`top_p` com HTTP
400 — por isso `supports_sampling=False`. Não reintroduza esses campos.

## Commits

```
<AREA>: <tipo> - <descrição curta>
```

Áreas deste repo: `API` | `PROVIDER` | `UI` | `AUTH` | `COST` | `DOCS` | `TEST`.
