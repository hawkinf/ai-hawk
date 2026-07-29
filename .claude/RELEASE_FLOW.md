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
| URL | `http://192.168.200.5:8081` |
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

## Expor em domínio (ainda não feito)

O proxy existente já atende `ai.hawk.com.br` e `ia.hawk.com.br` via cloudflared.
Para publicar o ai-hawk-server ali, seria preciso editar
`/opt/ai-hawk-proxy/nginx.conf` — arquivo de produção, mexer com cuidado.
O bloco necessário:

```nginx
location /ai-hawk/ {
    proxy_pass http://192.168.200.5:8081/;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_buffering off;          # obrigatorio para SSE, senao o streaming trava
    proxy_read_timeout 600s;
}
```

## Rollback

```bash
git log --oneline -5
git checkout <commit-anterior>
# reenvie com o comando de atualizar acima
```

Nenhum estado é persistido — rollback é imediato e sem migração.
