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

## HTTP 503 `gpu_busy` — host ai-hawk

> `⚠️ Não é possível carregar este modelo agora: outra pessoa está usando a GPU`

**Não é bug.** A RTX 3060 tem 12 GB e só cabe **um modelo grande por vez**. O
`hawk_swap_proxy.py` descarrega um backend para carregar outro; a troca leva
**~2 minutos**. A janela de "em uso" é `ACTIVE_WINDOW = 150s` sem requisições.

Os 4 backends são exclusivos entre si — `systemctl` mostra qual está de pé:

```bash
for u in ollama gemma4 qwen3coder gemma3ab; do printf "%-12s %s\n" "$u" "$(systemctl is-active $u)"; done
```

### Armadilha: o Ollama não libera a GPU sozinho

Um container do stack faz *polling* contínuo no gateway do Ollama. Enquanto o
Ollama estiver carregado, esse polling renova o "em uso" a cada ciclo e a janela
de 150s **nunca expira** — então o swap de volta para `gemma4` não acontece
sozinho. Sintoma: todo modelo llama.cpp passa a responder 503 indefinidamente.

Restaurar manualmente:

```bash
sudo systemctl stop ollama && sleep 3 && sudo systemctl start gemma4
```

O sentido inverso (`gemma4` → Ollama) funciona normalmente, porque ninguém faz
polling no gemma.

### Consequência prática

Os 10 modelos aparecem no catálogo, mas **não estão todos utilizáveis ao mesmo
tempo**. Alternar entre famílias custa ~2 min, e voltar do Ollama exige a
intervenção acima.

## 502 no proxy para um serviço do stack (host ai-hawk)

O container está `Up (healthy)` e responde na porta interna, mas o nginx devolve
502 e o log mostra `Host is unreachable` para um IP `172.18.0.x`.

**Causa:** o container foi recriado **sem** `--network ai-hawk-net` e nasceu
isolado na rede `bridge`. O nginx resolve os upstreams por nome dentro da
`ai-hawk-net` — de fora dela, nem o DNS resolve (`SERVFAIL`).

Diagnóstico:

```bash
docker inspect <container> --format '{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}'
docker exec ai-hawk-proxy sh -c 'wget -qO- http://<container>:8080/health'
```

Correção (aditiva, não recria o container):

```bash
docker network connect ai-hawk-net <container>
docker exec ai-hawk-proxy nginx -s reload   # re-resolve o IP
```

A conexão de rede persiste entre restarts do container. **Só se perde se ele
for recriado** (`docker rm` + `docker run`) sem a flag — sempre inclua
`--network ai-hawk-net` no comando de criação.

Conferir se alguém ficou de fora:

```bash
for c in $(docker ps --format '{{.Names}}'); do
  docker inspect $c --format '{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}' \
    | grep -q ai-hawk-net || echo "FORA: $c"
done
```

> `gemma4cuda` fica fora da rede **de propósito**: o nginx fala com ele pelo
> gateway do host (`172.18.0.1:8090/8191`), não por nome de container.

## Resposta vazia com `finish_reason: "length"`

`gemma4` e os `qwen3-*-thinking` são modelos de raciocínio: gastam o orçamento
de saída pensando antes de escrever. Com `max_tokens` baixo, todo o orçamento vai
para o raciocínio e o texto final sai vazio. Use `max_tokens` ≥ 2000.

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
