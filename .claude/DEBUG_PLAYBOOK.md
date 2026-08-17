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
for u in ollama gemma4 qwen3coder gemma4ab; do printf "%-12s %s\n" "$u" "$(systemctl is-active $u)"; done
```

### Autostart do Ollama (corrigido em 2026-07-29)

O `ollama.service` vinha `enabled` — subia sozinho no boot. Combinado com o
polling do open-webui, isso fixava a GPU logo na inicialização e **nenhum modelo
llama.cpp conseguia carregar** depois de um reboot.

Foi desabilitado (`systemctl disable ollama`), ficando igual aos outros três
backends. Todos agora sobem sob demanda, via `ensure()` do swap proxy — que tem
permissão para isso no `/etc/sudoers.d/hawksvc`. Estado correto:

```
ollama disabled | gemma4 disabled | qwen3coder disabled | gemma4ab disabled
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

## Modelo que não cabe inteiro na GPU — como medir

> Medido em 2026-08-16 com o `Qwen3.8-27B-IQ4_XS.gguf` (15,7 GB), que acabou
> **descartado** por ser lento demais. O método vale para o próximo candidato.

Um modelo de 15,7 GB não entra numa GPU de 12 GB: **nunca cabe inteiro**. Chutar `--n-gpu-layers` desperdiça VRAM ou estoura com
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
CUDA + KV `q8_0` de 8k). Daí saiu o valor usado: **`-ngl 46`** -> 11,1 GB,
deixando ~1,1 GB de folga. O `48` cabe, mas 707 MiB de sobra é arriscado.

Desempenho resultante: **6,9 tokens/s** na geração, 29 tokens/s lendo o prompt.
É o preço de manter ~25% das camadas na CPU. **Foi o motivo do descarte**: um
27B a 7 tokens/s não compensa perto do `gemma4`, que cabe inteiro. Use este
número como piso — abaixo disso, não vale virar backend.

Carga fria **medida** (com `echo 3 > /proc/sys/vm/drop_caches` e todos os
backends parados): **20s** do pedido à resposta, lendo 15,7 GB do disco. Ou
seja, os 75s que o `ensure()` espera pelo health sobram — não há o que ajustar
ali. Meça antes de mexer.

### Armadilha: `Up (unhealthy)` que não é problema nenhum

A imagem `llama.cpp:server-cuda` traz um `HEALTHCHECK` embutido que consulta
`localhost:8080`. Como cada backend do stack escuta numa porta própria (8090,
8092, 8094), o teste embutido falha sempre e o `docker ps` mostra
`Up (unhealthy)` com o modelo respondendo perfeitamente.

Confirme antes de investigar — se isto responde, está tudo bem:

```bash
curl -s http://127.0.0.1:8092/health
```

A correção é a unit declarar a sua própria checagem, como o `gemma4` já fazia:

```
--health-cmd "curl -sf http://localhost:<porta>/health || exit 1"
--health-interval 30s --health-start-period 300s
```

Aplicado em `qwen3coder` e `gemma4ab` em 2026-08-16 (o `gemma4` já tinha).

### Backend novo não aparece no open-webui

O open-webui **não usa o endpoint unificado (:8096)**. Ele tem uma conexão
OpenAI-compatível para **cada porteiro do swap**, cada uma com lista branca em
`model_ids`. Backend novo não aparece sozinho — precisa das duas coisas:

1. **Regra de UFW para a porta do porteiro.** O UFW libera por porta, uma a uma.
   Sem a regra, o container recebe `http=000` (nem conecta), e não 401/404:

```bash
docker exec open-webui curl -s -o /dev/null -w "%{http_code}
" http://host.docker.internal:<porteiro>/v1/models -H "Authorization: Bearer $(cat /etc/hawk/gateway.token)"
```

2. **Conexão nova na configuração.** Fica no SQLite, em três chaves paralelas
   que precisam ficar do mesmo tamanho: `openai.api_base_urls`,
   `openai.api_keys` e `openai.api_configs` (esta indexada por posição, string).
   O banco é `/var/lib/docker/volumes/open-webui/_data/webui.db`.

**Pare o container antes de editar o banco** — ele reescreve a configuração ao
desligar e desfaz a alteração. E faça backup: o arquivo tem 60 MB de conversas.

A chave dessas conexões é a mesma dos outros porteiros (`/etc/hawk/gateway.token`).

### Lista de modelos vazia no chat do ai-hawk

`-- nenhum modelo --` no seletor de `ia.hawk.com.br/ai-hawk/` quase sempre é
**falta da chave da API** no painel de Ajustes, não backend fora do ar: o
`/v1/models` exige `Authorization` e devolve 401. O próprio rodapé avisa
("Informe a chave da API em Ajustes"). Confirme por fora antes de investigar:

```bash
curl -s -o /dev/null -w "%{http_code}
" http://192.168.200.5:8081/v1/models -H "Authorization: Bearer $CHAVE"
```

### `apt upgrade` de biblioteca NVIDIA derruba TODO container de GPU

Sintoma: `systemctl start <backend>` fica em `activating`, o container nao
existe, a GPU esta vazia e o journal repete:

```
failed to fulfil mount request: open /usr/lib/x86_64-linux-gnu/libnvidia-egl-wayland.so.1.1.9: no such file or directory
```

Causa: `/var/run/cdi/nvidia.yaml` (especificacao CDI do runtime NVIDIA) fixa o
**caminho exato** de cada biblioteca do driver. Quando o `apt` atualiza uma
delas, a especificacao aponta para um arquivo que nao existe mais.

O que engana: **quem ja estava rodando continua funcionando** (a biblioteca
antiga esta montada no container vivo). A falha so aparece no proximo start -
que pode ser horas depois, quando o swap trocar de modelo. Aconteceu em
2026-08-16: o `apt` do instalador do Hermes Agent subiu `libnvidia-egl-wayland1`
de 1.1.9 para 1.1.21 as 19:52, e o stack so quebrou as 20:13.

Conserto:

```bash
sudo nvidia-ctk cdi generate --output=/var/run/cdi/nvidia.yaml
```

Confirme que a versao velha sumiu antes de reiniciar o backend:

```bash
sudo grep -c "1\.1\.9" /var/run/cdi/nvidia.yaml
```

`/var/run` e tmpfs e a especificacao e regerada no boot, entao **um reboot
tambem resolve** - o comando acima so faz o que o boot faria. Depois de
qualquer `apt upgrade` que toque em pacote `nvidia-*`, suba um container de
GPU para conferir, em vez de descobrir no proximo swap.

### Diagnostico apressado: 503 do swap nem sempre e lentidao de carga

Um `gpu_busy`/`indisponivel` depois de ~75s parece o laco de espera do
`ensure()` sendo curto demais. Foi o que pareceu em 2026-08-16 - e era o
container morrendo e reiniciando em laco pelo problema de CDI acima. A carga
real do mesmo modelo eram **7 segundos**.

Antes de mexer no timeout, olhe o journal do backend:

```bash
sudo journalctl -u qwen3coder -n 20 --no-pager
```

`restart counter is at N` com N alto significa laco de crash, nao carga lenta.

### Painel do Hermes Agent (`hermes-dashboard.service`)

Servico separado do ai-hawk, instalado no usuario `hawk` (nao root), codigo em
`~/.hermes`. Ouve em `0.0.0.0:9119` com o UFW liberando **so**
`192.168.200.0/24`, e exige senha (`HERMES_DASHBOARD_BASIC_AUTH_*` no
`~/.hermes/.env`, modo 600).

Apesar do nome, **nao e HTTP Basic**: e login por formulario com sessao. Testar
com `curl -u usuario:senha` devolve 401 mesmo com a senha certa e nao prova
nada. A rota real e:

```bash
curl -s -o /dev/null -w "%{http_code}
" -X POST http://127.0.0.1:9119/auth/password-login -H "Content-Type: application/json" -d '{"provider":"basic","username":"...","password":"..."}'
```

200 = credencial boa, 401 = ruim. **Nunca imprima o corpo da resposta**: um 422
por campo faltando ecoa a senha enviada em texto claro.

### Agente diz "empty content after retries" e o modelo parece burro

Sintoma: o cliente (Hermes, OpenClaw, qualquer laco de agente) responde
`No reply: the model returned empty content after retries`, ou o modelo escreve
a chamada de ferramenta como TEXTO cru:

```
<|tool_call>call:terminal_command{command: "ls"}<tool_call|>
```

Parece limitacao do modelo. Nao e - foram **dois** bugs do ai-hawk, os dois
corrigidos em 2026-08-16:

1. Ate a 0.1.0 o campo `tools` nao existia no schema e o Pydantic o descartava
   calado. O modelo nunca soube que havia ferramenta.
2. Ate a 0.2.0 o streaming so repassava deltas de texto. Como praticamente todo
   cliente streama por padrao, `tool_calls` sumia no caminho e chegava vazio.

Antes de culpar o modelo, repita **sem streaming**:

```bash
curl -s http://127.0.0.1:8081/v1/chat/completions -H "Authorization: Bearer $CHAVE"   -H 'Content-Type: application/json'   -d '{"model":"hawk/gemma4","messages":[{"role":"user","content":"que horas sao? use a ferramenta"}],"max_tokens":2000,"tools":[{"type":"function","function":{"name":"agora","parameters":{"type":"object","properties":{}}}}]}'   | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['choices'][0]['finish_reason'])"
```

`tool_calls` = o modelo esta bem, o problema esta no transporte.

E nao adianta desligar streaming pelo cliente: no Hermes, `display.streaming`
controla a exibicao, nao o modo da chamada. Confirme pelo log de quem recebe:

```bash
sudo journalctl -u ai-hawk-server -n 20 --no-pager | grep -o "stream=[A-Za-z]*"
```

### Trocar o modelo de um backend (em vez de criar outro)

Modelo novo raramente justifica backend novo: a GPU so carrega **um por vez**,
entao o quinto backend nao da acesso simultaneo - da mais unit, porta, regra de
UFW e conexao de open-webui para manter. Quando o modelo novo SUBSTITUI o
antigo, troque dentro do backend existente.

Feito em 2026-08-16, com o Gemma 3 abliterated saindo para o Gemma 4
abliterated. Renomear o id (`gemma3ab` -> `gemma4ab`) espalha por mais lugares
do que parece - esqueça um e o swap quebra em silencio:

1. `/etc/systemd/system/<nome>.service` (arquivo, `-m`, `--mmproj`, `-a`, nome
   do container em `--name`/`ExecStartPre`/`ExecStop`, `Description`)
2. `Conflicts=` das outras units llama.cpp - todas se citam mutuamente
3. `/etc/sudoers.d/hawksvc` (start e stop)
4. `hawk_swap_proxy.py`: constantes de porta e model id, `UNITS`, `HEALTH`,
   `UNIFIED_LLAMACPP`, `route_backend` e o listener do porteiro
5. Lista branca `model_ids` da conexao no open-webui
6. Lista branca `model_ids` da conexao no open-webui
7. Tabela de modelos do `INTEGRACAO.md`

E os que ninguem lembra - foram encontrados so pela varredura, depois de a
troca ja parecer pronta:

8. `/opt/hawk/watchdog.sh` (o `check_and_heal <id> <porta>` tentaria curar um
   servico inexistente)
9. Os auxiliares de interface em `/opt/hawk/`: `set_menu.py`, `update_icons.py`,
   `update_colors.py`, `update_funcicons.py`, `colors_multicat.py` - mapeiam
   icone e cor por id de modelo
10. `comfyui.service` - **nao e llama.cpp, mas disputa a mesma GPU** e tem
    `Conflicts=` nos backends

Nao confie na lista: rode a varredura, e **sem `head`**, que ja escondeu
metade dos arquivos aqui:

```bash
sudo grep -rl "<id-antigo>" /opt/hawk/ /etc/systemd/system/ /etc/sudoers.d/ | grep -v "\.bak"
```

Depois de tocar qualquer `.service`, `systemctl daemon-reload`.

Aproveite a troca para revisar o `-c`: o Gemma 3 ab rodava com 8k, e o
substituto ficou com 32k + KV `q8_0` ocupando 8,6 GB dos 12 GB.

### Gateway do Hermes (Telegram) - o que confunde

Servico de **usuario**, nao de sistema. Comandos mudam de forma:

```bash
systemctl --user status hermes-gateway
journalctl --user -u hermes-gateway -n 30 --no-pager
```

Para dirigir como root, tem que emprestar a sessao do usuario, senao o
systemctl nao encontra nada:

```bash
sudo -u hawk XDG_RUNTIME_DIR=/run/user/1000 systemctl --user restart hermes-gateway
```

Confira `loginctl show-user hawk --property=Linger`. Sem `Linger=yes` o servico
morre quando a sessao do usuario termina - inclusive no reboot.

**O Telegram nao registra linha de sucesso.** O log para em
`Connecting to Telegram (attempt 1/8)` e fica assim mesmo funcionando, o que
faz parecer travado. Para saber se esta realmente consumindo, provoque um
conflito: o Telegram so aceita UM consumidor de `getUpdates`.

```bash
T=$(grep -oP '^TELEGRAM_BOT_TOKEN=\K.*' ~/.hermes/.env)
curl -s "https://api.telegram.org/bot$T/getUpdates?timeout=1&limit=1" | head -c 200
```

**409 Conflict = esta conectado** (o gateway e o outro consumidor). `200 ok`
com a lista vazia significa que ninguem esta ouvindo.

**Controle de acesso.** `TELEGRAM_ALLOWED_USERS` e o perimetro inteiro - quem
esta na lista manda o agente executar comando no servidor. No codigo do
adaptador:

```python
if not allowed_csv:
    return True          # lista vazia = TODO MUNDO autorizado
```

`*` na lista tem o mesmo efeito. Com a lista preenchida, DM de desconhecido e
ignorada em silencio (regra 5 do `_get_unauthorized_dm_behavior`), sem cair no
fluxo de pareamento.

**Mensagem de erro que mente:** o aviso do WhatsApp manda "remove
WHATSAPP_ENABLED from your .env", mas o `hermes gateway setup` grava a
plataforma no **config.yaml**, em `platforms.whatsapp.enabled`. Procurar no
`.env` nao acha nada. Plataforma habilitada e nao pareada derruba o gateway
com `status=1/FAILURE` na partida.

### O watchdog desfazia as trocas do swap (corrigido em 2026-08-16)

Sintoma: notificacao "[backend] estava em failed, reiniciei e voltou" e, um
segundo depois, o backend que o swap tinha acabado de subir **cai**. A GPU fica
com o modelo errado e ninguem entende por que.

Causa: dois sistemas com ideias opostas. O `hawk-watchdog.timer` roda a cada 5
minutos e o `check_and_heal` reinicia qualquer unit em `failed`. So que parar um
backend pelo swap **deixa a unit em `failed`** (docker stop estoura o prazo,
SIGKILL, exit 137). O watchdog lia isso como queda, ressuscitava o backend, e o
`Conflicts=` derrubava o que estava certo.

Correcao: o `ensure()` do swap agora roda `systemctl reset-failed` logo depois
de cada `stop`. Assim `failed` volta a significar "caiu de verdade" - o watchdog
segue curando queda real e para de desfazer troca normal. Precisou de
`reset-failed` no `/etc/sudoers.d/hawksvc`, porque o proxy roda como `hawksvc`.

> **Atencao ao ler o estado do watchdog:** `systemctl is-active hawk-watchdog`
> devolve `inactive` e `is-enabled` devolve `static` MESMO funcionando - ele e
> `oneshot` disparado por timer. Confira o timer, nao o servico:
>
> ```bash
> systemctl list-timers hawk-watchdog.timer --no-pager
> ```

### Backend em `failed` depois de uma troca e normal

`systemctl status` mostrando `failed` com `status=137/n/a` logo apos um swap
nao e defeito: 137 e SIGKILL (128+9). O `docker stop` estoura o prazo, o
container e morto, e como `qwen3coder` e `gemma4ab` tem `Restart=on-failure`,
o systemd marca a unit como falha. O `gemma4` nao mostra isso porque usa
`Restart=no`.

Nao troque para `Restart=no` so pelo estado bonito: `on-failure` levanta o
container quando ele cai de verdade (OOM, por exemplo). O `Conflicts=` ja
impede que o systemd o ressuscite durante a troca. E `ensure()` inicia unit em
estado `failed` sem problema, entao nada quebra.

Para limpar a poluicao visual:

```bash
sudo systemctl reset-failed <backend>
```

### Modelo de nuvem que aparece na listagem e devolve 404

A listagem de modelos de um provedor **nao prova** que o modelo responde. Em
2026-08-16, `gemini-2.5-flash` e `gemini-2.5-flash-lite` constavam no
`/v1beta/models` da chave e devolviam 404 na chamada pelo dialeto OpenAI - que
e o caminho que o ai-hawk usa. O `gemini-2.5-pro`, mesma familia, respondia
normal. O `gemini-2.0-flash` que estava no `_GOOGLE_MODELS` tambem ja tinha
morrido sem ninguem notar.

Catalogo com modelo morto so aparece como erro na cara do cliente. Ao mexer em
`_GOOGLE_MODELS` (ou qualquer lista estatica), teste **cada id pelo endpoint
real**, nao pela listagem:

```bash
K=$(sudo grep -oP '^HAWK_API_KEYS=\K[^,]*' /opt/ai-hawk-server/.env)
for M in gemini-3.7-flash gemini-3.6-flash; do
  curl -s -m 60 http://127.0.0.1:8081/v1/chat/completions -H "Authorization: Bearer $K"     -H 'Content-Type: application/json'     -d "{\"model\":\"google/$M\",\"messages\":[{\"role\":\"user\",\"content\":\"ok\"}],\"max_tokens\":2000}"     | head -c 120; echo " <- $M"
done
```

**503 "high demand" nao e modelo morto.** O free tier da Google sobrecarrega e
volta na tentativa seguinte - aconteceu no meio do teste e sumiu sozinho.
Repita antes de concluir que esta indisponivel.

### Busca web do agente sai de graca pelo searxng do proprio host

O Hermes aceita `SEARXNG_URL` no `~/.hermes/.env` - sem chave de API e sem
custo, usando o container que ja roda neste host. Duas pegadinhas:

1. O container **nao publica porta**, e o Hermes roda fora do Docker. Use o IP
   dele na bridge. **Esse IP muda se o container for recriado**; redescubra com:

```bash
docker inspect searxng --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}'
```

2. O searxng vem com **formato JSON desligado** por padrao. Aqui ja estava
   ligado (`search.formats: [html, json]` em `/etc/searxng/settings.yml`), mas
   se a busca voltar vazia, e o primeiro lugar para olhar.

Confirme antes de culpar o agente:

```bash
curl -s "http://<ip>:8080/search?q=teste&format=json" | head -c 120
```

### Cota da nuvem acaba: use a cadeia de reserva

O free tier do Gemini tem limite por minuto E por dia, e o diario acaba mais
rapido do que parece - um dia de testes bastou. Quando acaba, vem `429` e o
agente fica **mudo**. Para um agente que atende no Telegram isso e inaceitavel.

O Hermes tem `fallback_providers` no topo do `config.yaml` - lista de
`{provider, model, base_url}` tentada em ordem quando a primaria falha por
rate-limit, 5xx ou erro de conexao:

```yaml
fallback_providers:
  - provider: custom
    model: hawk/gemma4
    base_url: http://127.0.0.1:8081/v1
```

`hermes fallback add` faz o mesmo, mas so no modo interativo. Confira com
`hermes fallback list`.

Assim a nuvem cuida do dia a dia e o modelo local segura quando a cota estoura,
sem ninguem perceber. Verificado em 2026-08-16 com a cota ja esgotada: o agente
respondeu pelo `gemma4` em 7s sem intervencao.

### Cron do Hermes: ordem dos argumentos e o monitor que economiza cota

O `prompt` e posicional e, junto com `--monitor-script`, **tem que vir logo
depois do `schedule`**. Com as opcoes antes do prompt o argparse recusa com um
"unrecognized arguments" que mostra a ajuda do comando raiz e nao a do cron -
o que faz parecer erro de sintaxe do prompt.

```bash
# funciona
hermes cron create "0 11 * * *" "PROMPT" --name X --deliver telegram --monitor-script s.sh
# falha
hermes cron create "0 11 * * *" --monitor-script s.sh --name X "PROMPT"
```

Prompt longo com quebras de linha passa bem, desde que venha por
`"$(cat arquivo)"` DENTRO do shell que executa o comando. Variavel exportada
nao sobrevive ao `sudo -u hawk -i`, que zera o ambiente.

**`--monitor-script` e o que torna vigilancia barata.** O script roda a cada
tick; saida identica (hash byte a byte) **suprime o agente inteiro**, entao dia
parado nao consome token nem cota de nuvem. So mudanca acorda o LLM, ja com o
diff no prompt.

Duas regras para o script: saida **estavel** (nada de data/hora, que mudaria
sempre) e falha de rede virando **texto fixo** - se o erro mudar de forma a cada
vez, vira alarme diario. Exemplo em `~/.hermes/scripts/upstream-versions.sh`,
que imprime `indisponivel` quando a API nao responde.

### Hermes tenta modelo PAGO por conta propria (pista auxiliar)

Alem do modelo principal, o Hermes usa um "auxiliary client" para tarefas
pequenas - gerar titulo de conversa, por exemplo. **Essa pista aceita SKU pago
por padrao.** No log:

```
WARNING agent.auxiliary_client: PAID lane engaged for auxiliary task —
OpenRouter fallback model 'google/gemini-3.6-flash' is not a :free SKU and
may incur real spend. Set auxiliary.free_only: true to restrict auxiliary...
```

Aqui nao houve gasto so porque nao ha chave do OpenRouter configurada - o log
segue com `credential pool: no available entries` e marca o provedor como
`unhealthy (payment / credit error)`. Ou seja, a protecao era **acidental**.

Para quem tem regra de custo zero, torne estrutural:

```bash
hermes config set auxiliary.free_only true
```

Ligado em 2026-08-17. Vale conferir depois de cada `hermes update`.

### Identidade que o modelo declara NAO e prova

Perguntar "qual modelo esta te respondendo" e confiar na resposta e erro: LLM
alucina a propria identidade. Numa verificacao aqui, a resposta veio "Eu sou o
Gemini 3.7 Flash" enquanto a duvida era justamente se a cadeia de reserva tinha
caido para o local. Confirme pelo log, com hora:

```bash
grep -E "conversation turn|Turn ended" ~/.hermes/logs/agent.log | tail -2
```

A linha traz `model=` com o id real. Cuidado ao filtrar: `tail` sobre um grep
mal montado devolve turno antigo e faz parecer que a execucao nao registrou.

### Mapa de portas dos backends

| backend | container | porteiro no swap |
|---|---|---|
| ollama | 11434 | 11435 |
| gemma4 | 8090 | 8091 |
| qwen3coder | 8092 | 8093 |
| gemma4ab | 8094 | 8095 |

**8096 é o endpoint unificado**, não sobra para backend novo. O
`ai-hawk-server` (:8081) consome o unificado; o open-webui consome os
porteiros um a um. Cada porteiro precisa da sua regra de UFW.

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
