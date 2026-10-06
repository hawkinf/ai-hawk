#!/bin/bash
# hawk-update-ollama.sh - mantem o Ollama do servidor na ultima versao, com validacao e rollback.
# Roda como root (timer hawk-ollama-update.timer) ou manualmente: sudo hawk-update-ollama.sh [--force]
# Instalacao manual (tar.zst) em /usr/local; as configuracoes ficam nos drop-ins do systemd e NAO sao tocadas.
set -uo pipefail

LOG=/var/log/hawk-ollama-update.log
BK_ROOT=/opt/ollama-backup
exec >>"$LOG" 2>&1
echo "=== $(date -Is)"

# versao do BINARIO (sem consultar o servidor em execucao, que responderia com a versao dele)
binver() { OLLAMA_HOST=127.0.0.1:1 "$1" --version 2>&1 | grep -oE '(client version|ollama version) is [0-9]+[.][0-9]+[.][0-9]+' | tail -1 | grep -oE '[0-9]+[.][0-9]+[.][0-9]+'; }

cur=$(binver /usr/local/bin/ollama)
latest=$(curl -fsSL -m 25 https://api.github.com/repos/ollama/ollama/releases/latest | grep -m1 '"tag_name"' | grep -oE '[0-9]+\.[0-9]+\.[0-9]+')
if [ -z "${latest:-}" ]; then echo "nao consegui ver a ultima versao (sem rede?)"; exit 0; fi
echo "instalada=${cur:-?} ultima=$latest"

newest=$(printf '%s\n%s\n' "${cur:-0.0.0}" "$latest" | sort -V | tail -1)
if [ "$newest" = "${cur:-0.0.0}" ] && [ "${1:-}" != "--force" ]; then echo "ja esta atualizado"; exit 0; fi

tmp=$(mktemp -d /var/tmp/ollama-up.XXXXXX)
trap 'rm -rf "$tmp"' EXIT

echo "baixando v$latest..."
if ! curl -fL --retry 3 -m 3600 -o "$tmp/o.tar.zst" "https://github.com/ollama/ollama/releases/download/v$latest/ollama-linux-amd64.tar.zst"; then
  echo "FALHA no download"; exit 1
fi
if ! zstd -tq "$tmp/o.tar.zst"; then echo "arquivo corrompido"; exit 1; fi

mkdir -p "$tmp/x"
zstd -dc "$tmp/o.tar.zst" | tar -x -C "$tmp/x" || { echo "FALHA ao extrair"; exit 1; }
if [ ! -x "$tmp/x/bin/ollama" ]; then echo "pacote sem bin/ollama (layout mudou?)"; ls "$tmp/x" | head; exit 1; fi
new=$(binver "$tmp/x/bin/ollama")
echo "binario novo reporta versao: ${new:-?}"
[ "${new:-}" = "$latest" ] || { echo "versao do binario nao confere; abortando"; exit 1; }

bk="$BK_ROOT/${cur:-desconhecida}-$(date +%Y%m%d%H%M%S)"
mkdir -p "$bk"
cp -a /usr/local/bin/ollama "$bk/ollama"
[ -d /usr/local/lib/ollama ] && cp -a /usr/local/lib/ollama "$bk/lib"
echo "backup em $bk"

systemctl stop ollama
install -m 0755 "$tmp/x/bin/ollama" /usr/local/bin/ollama
if [ -d "$tmp/x/lib/ollama" ]; then rm -rf /usr/local/lib/ollama; cp -a "$tmp/x/lib/ollama" /usr/local/lib/ollama; fi
systemctl start ollama

ok=0
for i in $(seq 1 40); do
  v=$(curl -fsS -m 3 http://127.0.0.1:11434/api/version 2>/dev/null || true)
  case "$v" in *"$latest"*) ok=1; break;; esac
  sleep 2
done

if [ "$ok" = 1 ]; then
  echo "OK: Ollama atualizado para $latest"
  # guarda so os 3 backups mais recentes
  ls -1dt "$BK_ROOT"/* 2>/dev/null | tail -n +4 | xargs -r rm -rf
else
  echo "FALHOU apos atualizar: voltando para ${cur:-?}"
  systemctl stop ollama
  install -m 0755 "$bk/ollama" /usr/local/bin/ollama
  if [ -d "$bk/lib" ]; then rm -rf /usr/local/lib/ollama; cp -a "$bk/lib" /usr/local/lib/ollama; fi
  systemctl start ollama
  exit 1
fi
