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

## Deploy na VPS (contaslite)

```bash
ssh contaslite
cd /var/www/ai-hawk && git pull
.venv/bin/pip install -r requirements.txt
systemctl restart ai-hawk
systemctl status ai-hawk --no-pager
curl -s localhost:8080/health | jq
```

### systemd — `/etc/systemd/system/ai-hawk.service`

```ini
[Unit]
Description=ai-hawk
After=network.target

[Service]
WorkingDirectory=/var/www/ai-hawk
EnvironmentFile=/var/www/ai-hawk/.env
ExecStart=/var/www/ai-hawk/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8080
Restart=always
User=www-data

[Install]
WantedBy=multi-user.target
```

### nginx

Streaming exige buffer desligado, senão a resposta chega toda de uma vez:

```nginx
location / {
    proxy_pass http://127.0.0.1:8080;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_buffering off;          # <- obrigatorio para SSE
    proxy_read_timeout 600s;      # <- respostas longas
}
```

## Rollback

```bash
git log --oneline -5
git checkout <commit-anterior>
systemctl restart ai-hawk
```

Nenhum estado é persistido, então rollback é imediato e sem migração.
