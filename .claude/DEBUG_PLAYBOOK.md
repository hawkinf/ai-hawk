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

Os 5 backends são exclusivos entre si — `systemctl` mostra qual está de pé:

```bash
for u in ollama gemma4 qwen3coder gemma3ab qwen38; do printf "%-12s %s\n" "$u" "$(systemctl is-active $u)"; done
```

### Autostart do Ollama (corrigido em 2026-07-29)

O `ollama.service` vinha `enabled` — subia sozinho no boot. Combinado com o
polling do open-webui, isso fixava a GPU logo na inicialização e **nenhum modelo
llama.cpp conseguia carregar** depois de um reboot.

Foi desabilitado (`systemctl disable ollama`), ficando igual aos outros três
backends. Todos agora sobem sob demanda, via `ensure()` do swap proxy — que tem
permissão para isso no `/etc/sudoers.d/hawksvc`. Estado correto:

```
ollama disabled | gemma4 disabled | qwen3coder disabled | gemma3ab disabled | qwen38 disabled
```

Se algum voltar a `enabled`, a armadilha de reboot volta junto.

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

---

## Modelo que não cabe inteiro na GPU (medido em 2026-08-16)

O `Qwen3.8-27B-IQ4_XS.gguf` tem 15,7 GB e a RTX 3060 tem 12 GB: **ele nunca cabe
inteiro**. Chutar `--n-gpu-layers` desperdiça VRAM ou estoura com
`ggml_backend_cuda_buffer_type_alloc_buffer`. O certo é medir, subindo o servidor
com vários `-ngl` e lendo `nvidia-smi`:

| `-ngl` | resultado | VRAM |
|---|---|---|
| 99 | **CUDA OOM** | — |
| 48 | carrega | 11.581 MiB |
| 40 | carrega | 9.839 MiB |
| 34 | carrega | 8.541 MiB |
| 30 | carrega | 7.671 MiB |
| 26 | carrega | 6.799 MiB |
| 22 | carrega | 5.929 MiB |

A relação é linear: **217 MiB por camada**, mais ~1.150 MiB de base (contexto
CUDA + KV `q8_0` de 8k). Daí sai o valor em produção: **`-ngl 46`** -> 11,1 GB,
deixando ~1,1 GB de folga. O `48` cabe, mas 707 MiB de sobra é arriscado.

Desempenho resultante: **6,9 tokens/s** na geração, 29 tokens/s lendo o prompt.
É o preço de manter ~25% das camadas na CPU — utilizável, não rápido. Por ser
modelo de raciocínio, vale a regra do `max_tokens` >= 2000.

Carga fria **medida** (com `echo 3 > /proc/sys/vm/drop_caches` e todos os
backends parados): **20s** do pedido à resposta, lendo os 15,7 GB do disco. Ou
seja, os 75s que o `ensure()` espera pelo health sobram — não há o que ajustar
ali. Meça antes de mexer.

### Armadilha: `Up (unhealthy)` que não é problema nenhum

A imagem `llama.cpp:server-cuda` traz um `HEALTHCHECK` embutido que consulta
`localhost:8080`. Como cada backend do stack escuta numa porta própria (8090,
8092, 8094, 8098), o teste embutido falha sempre e o `docker ps` mostra
`Up (unhealthy)` com o modelo respondendo perfeitamente.

Confirme antes de investigar — se isto responde, está tudo bem:

```bash
curl -s http://127.0.0.1:8098/health
```

A correção é a unit declarar a sua própria checagem, como o `gemma4` já fazia:

```
--health-cmd "curl -sf http://localhost:8098/health || exit 1"
--health-interval 30s --health-start-period 300s
```

Aplicado em `qwen38`, `qwen3coder` e `gemma3ab` em 2026-08-16.

### Mapa de portas dos backends

| backend | container | porteiro no swap |
|---|---|---|
| ollama | 11434 | 11435 |
| gemma4 | 8090 | 8091 |
| qwen3coder | 8092 | 8093 |
| gemma3ab | 8094 | 8095 |
| qwen38 | 8098 | 8099 |

**8096 é o endpoint unificado**, não sobra para backend novo.

### Armadilha: `llama-cli` manual segura a GPU para sempre

Um teste solto com `/root/llama.cpp/llama-cli -p "..." -n 64` **não termina**: o
llama-cli entra em modo conversa depois de gerar e fica esperando entrada, com o
modelo carregado. Sintoma: processo com dezenas de minutos de `ELAPSED`, 11 GB de
VRAM presos e **nenhum backend do stack conseguindo subir** (todos `inactive`,
`ensure()` sempre falhando).

Em teste manual use `-no-cnv`. Para conferir se é isso:

```bash
nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv,noheader
ps -o pid,lstart,etime,args -p <PID>
```

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

Para o open-webui existe um script com o comando correto (preserva o volume
de dados: contas, chats e configurações):

```bash
sudo /opt/hawk/recreate-open-webui.sh
```

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
