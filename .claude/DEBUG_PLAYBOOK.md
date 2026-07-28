# DEBUG_PLAYBOOK — ai-hawk

## Primeiro comando, sempre

```bash
curl http://localhost:8080/health
```

Mostra provedores ativos, estado do guarda de custo e se a autenticação está ligada.

---

## `/v1/models` volta vazio

| Causa | Verificação | Correção |
|---|---|---|
| Nenhum provedor configurado | `/health` → `providers: []` | Suba o Ollama, ou `python scripts/mock_provider.py` |
| Ollama não está rodando | `curl http://localhost:11434/v1/models` | Inicie o Ollama |
| Chave de free tier inválida | log com `Descoberta de modelos falhou em <nome>` | Refaça a chave no `.env` |
| Cache de 2 minutos | — | `GET /v1/models?refresh=true` |

A descoberta de catálogo **nunca** derruba a rota: falha de um provedor só o
remove da lista e gera um `WARNING`.

---

## HTTP 402 ao usar um modelo

Comportamento esperado — o guarda de custo bloqueou um modelo pago.
Para liberar (e passar a gastar): `ALLOW_PAID_MODELS=true` no `.env` e reiniciar.

## HTTP 404 `model_not_found`

O id não está no catálogo. Liste com `/v1/models`. Ids têm a forma
`provedor/modelo`. Se o provedor pago nem foi configurado (sem chave no `.env`),
o erro é 404 e não 402 — não há o que bloquear.

## HTTP 401

`HAWK_API_KEYS` está preenchido e o cliente não mandou `Authorization: Bearer <chave>`,
ou mandou uma chave que não está na lista.

## HTTP 503 `nao foi possivel conectar`

O provedor upstream está fora do ar. Para Ollama, é o caso mais comum:
o serviço não está rodando na porta 11434.

---

## Streaming corta no meio

O erro **não** aparece no status HTTP (já foi 200). Ele é emitido como um evento
no fluxo:

```
data: {"error": {"message": "...", "type": "provider_error"}}
data: [DONE]
```

Clientes precisam checar `chunk.error` antes de ler `choices`. A interface web
(`web/chat.js`) e todos os exemplos em `examples/` já fazem isso.

---

## Resposta de modelo Claude falha com 400

Quase sempre é `temperature`/`top_p` sendo enviados para a família 5, que os
rejeita. Confirme `supports_sampling=False` no `ModelSpec` correspondente.

---

## Reproduzir sem gastar API

```bash
python scripts/mock_provider.py     # sobe na porta 11434 (mesma do Ollama)
```

Ele fala o dialeto OpenAI, faz streaming e responde com um eco. Serve para
depurar rotas, interface e clientes sem tocar em nenhum provedor real.
