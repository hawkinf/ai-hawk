# BACKUP_POLICY — ai-hawk

## O que precisa de backup

| Item | Onde | Backup |
|---|---|---|
| Código | git | O próprio repositório |
| `.env` (chaves) | fora do git | **Gerenciador de senhas** — nunca em repositório |
| Histórico de conversas | `localStorage` do navegador | Não há — é volátil por design |
| Modelos Ollama | `~/.ollama/models` | Não precisa: `ollama pull` refaz |

## O que NUNCA vai para o git

- `.env` (bloqueado pelo `.gitignore` — confirme antes de cada commit)
- Qualquer chave de API, em código, teste, log ou documentação
- Dumps de conversa contendo dados de cliente

## Rotação de chaves

Se uma chave vazar:

1. Revogue no painel do provedor (Groq, OpenRouter, Cerebras, Google, etc.).
2. Gere a nova e atualize o `.env` da VPS.
3. `systemctl restart ai-hawk`.
4. Se foi uma chave `HAWK_API_KEYS`, atualize também todos os programas clientes.

## Estado do servidor

O ai-hawk é **stateless**: nada é gravado em disco além de log. Pode ser
recriado do zero com `git clone` + `.env`. Não há banco, migração ou volume
a restaurar.
