# RELEASE_FLOW — ai-hawk

## Checklist antes de subir

- [ ] `ruff check .` → 0 erros
- [ ] `pytest` → todos verdes
- [ ] `/health` responde com os provedores esperados
- [ ] Chat web envia e recebe (streaming e não-streaming)
- [ ] `HAWK_API_KEYS` preenchido — **obrigatório** fora de localhost
- [ ] `ALLOW_PAID_MODELS` no valor pretendido (padrão: `false`)
- [ ] `.env` **não** está no commit (confira o `.gitignore`)
- [ ] Versão bumpada em `app/__init__.py` e `pyproject.toml`

## Produção atual — host `ai-hawk` (192.168.200.5)

| Item | Valor |
|---|---|
| Caminho | `/opt/ai-hawk-server` |
| Serviço | `ai-hawk-server.service` (systemd, habilitado no boot) |
| Porta | `8081`, liberada no UFW **só** para `192.168.200.0/24` |
| URL (LAN) | `http://192.168.200.5:8081` |
| URL (público) | `https://ia.hawk.com.br/ai-hawk/` |
| Backend | `litellm` em `127.0.0.1:4000` → modelos locais na RTX 3060 |
| Guarda de custo | **ativa** (`ALLOW_PAID_MODELS=false`) |
| Autenticação | ativa (`HAWK_API_KEYS` preenchido) |

Esta máquina já roda um stack de IA (litellm, librechat, lobechat, open-webui,
ComfyUI, llama.cpp, cloudflared) atrás do proxy `ai-hawk-proxy` nas portas
80/443. **O ai-hawk-server foi instalado ao lado, sem tocar em nada disso.**

### Atualizar

Do Windows, na raiz do repositório:

```bash
git archive --format=tar HEAD | ssh -o RemoteCommand=none -o RequestTTY=no ai-hawk 'sudo tar -x -C /opt/ai-hawk-server && sudo chown -R hawk:hawk /opt/ai-hawk-server'
ssh -o RemoteCommand=none -o RequestTTY=no ai-hawk '/opt/ai-hawk-server/.venv/bin/pip install -q -r /opt/ai-hawk-server/requirements.txt && sudo systemctl restart ai-hawk-server'
```

`git archive` envia só o que está commitado — o `.env` e a `.venv` do servidor
nunca são sobrescritos.

> O alias `ai-hawk` tem `RemoteCommand sudo -i` no `~/.ssh/config`. Para rodar
> comandos diretos é obrigatório passar `-o RemoteCommand=none -o RequestTTY=no`.

### Verificar

```bash
ssh -o RemoteCommand=none -o RequestTTY=no ai-hawk 'systemctl status ai-hawk-server --no-pager -n 20'
curl -s http://192.168.200.5:8081/health
```

### Logs

```bash
ssh -o RemoteCommand=none -o RequestTTY=no ai-hawk 'sudo journalctl -u ai-hawk-server -f'
```

## Acesso público — `https://ia.hawk.com.br/ai-hawk/`

| Rota | Auth na borda | Destino |
|---|---|---|
| `/ai-hawk/` | não (é só a UI) | `172.18.0.1:8081/` |
| `/ai-hawk/v1/…` | **sim** (`Authorization` obrigatório) | `172.18.0.1:8081/v1/` |
| `/ai-hawk` | — | 301 para `/ai-hawk/` |

Duas camadas: o nginx exige a *presença* do header `Authorization` nas rotas de
API, e o ai-hawk-server valida a chave de fato contra `HAWK_API_KEYS`.

### Qual domínio passa pelo proxy

O túnel Cloudflare roteia de forma diferente — confira antes de mexer:

| Domínio | Vai para |
|---|---|
| `ai.hawk.com.br` | **direto ao open-webui**, não passa pelo nginx |
| `ia.hawk.com.br` | `ai-hawk-proxy:80` → nginx |

Editar o bloco `ai.hawk.com.br` do nginx **não afeta tráfego público** — ele só
atende a LAN (`192.168.200.5`, `ai-hawk.local`).

### Regras de firewall necessárias

A porta 8081 precisa de duas regras: LAN (acesso direto) e Docker (o container
nginx alcança o host por `172.18.0.1`).

```bash
sudo ufw allow from 192.168.200.0/24 to any port 8081 proto tcp
sudo ufw allow from 172.16.0.0/12   to any port 8081 proto tcp
```

### Mexer no nginx com segurança

`/opt/ai-hawk-proxy/nginx.conf` é produção com túnel ativo. Sempre nesta ordem:

```bash
sudo cp -p /opt/ai-hawk-proxy/nginx.conf /opt/ai-hawk-proxy/nginx.conf.bak-$(date +%Y%m%d_%H%M%S)
# editar...
docker exec ai-hawk-proxy nginx -t        # valida SEM aplicar
docker exec ai-hawk-proxy nginx -s reload # reload, nao restart
```

Rollback: restaure o `.bak-*` e dê reload de novo.

> **`proxy_buffering off` é obrigatório** nas rotas de API. Sem isso o nginx
> segura a resposta inteira e o streaming SSE chega de uma vez só.

### Cache de CDN — armadilha já vivida

O Cloudflare cacheou o `chat.js` e continuou servindo a versão antiga depois de
um deploy (`cf-cache: HIT`), fazendo a correção publicada simplesmente não
chegar ao navegador. Duas defesas, ambas já no lugar:

- O HTML sai com `Cache-Control: no-store` (na aplicação **e** no nginx).
- As URLs dos assets levam `?v=<hash do conteúdo>`, calculado no start.
  Mudou o arquivo, muda a URL — nenhum cache intermediário consegue servir
  versão velha, e não existe bump manual de versão para esquecer.

Para conferir após um deploy:

```bash
curl -sI https://ia.hawk.com.br/ai-hawk/ | grep -i "cache-control\|cf-cache"
```

## Rollback

```bash
git log --oneline -5
git checkout <commit-anterior>
# reenvie com o comando de atualizar acima
```

Nenhum estado é persistido — rollback é imediato e sem migração.
