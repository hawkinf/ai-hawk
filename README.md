# ai-hawk

Servidor de IA da Hawk Informática. Faz duas coisas:

1. **Chat web** — interface em `http://localhost:8080` onde você escolhe qual LLM usar.
2. **API para seus programas** — compatível com a da OpenAI, então qualquer SDK
   existente funciona. Você escolhe a LLM pelo campo `model` da requisição.

Um único servidor na frente de vários provedores. Trocar de modelo é trocar uma string.

---

## Custo zero por padrão

O servidor nasce com o **guarda de custo ligado** (`ALLOW_PAID_MODELS=false`).
Enquanto ele estiver ativo:

- só aparecem modelos gratuitos em `GET /v1/models`;
- qualquer tentativa de usar modelo pago retorna **HTTP 402** antes de qualquer
  chamada à rede — nenhum token é gasto.

| Provedor | Custo | O que precisa |
|---|---|---|
| **Ollama** | zero absoluto | roda na sua máquina, sem chave nenhuma |
| **Backend local** | zero absoluto | litellm, llama.cpp, vLLM, LM Studio — qualquer um que fale o dialeto OpenAI |
| **Groq** | free tier | chave gratuita, sem cartão |
| **OpenRouter** | free tier | só modelos com sufixo `:free` são expostos |
| **Cerebras** | free tier | chave gratuita |
| **Google Gemini** | free tier | chave do AI Studio (modelos Flash) |
| Anthropic (Claude) | pago | **bloqueado** até você liberar |
| OpenAI (GPT) | pago | **bloqueado** até você liberar |

Para liberar os pagos depois, edite o `.env`: `ALLOW_PAID_MODELS=true`.

---

## Instalação

```bash
cd C:\develop\ai-hawk
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
copy .env.example .env
```

### Opção A — custo zero absoluto (Ollama)

Instale o [Ollama](https://ollama.com), baixe um modelo e ele é detectado sozinho:

```bash
ollama pull llama3.2
```

### Opção B — sem instalar nada (provedor simulado)

Para testar o servidor antes de configurar qualquer provedor:

```bash
.venv\Scripts\python.exe scripts/mock_provider.py
```

Ele sobe na mesma porta do Ollama (`11434`) e responde com um eco. Útil para
validar a interface e a integração dos seus programas.

### Opção C — free tiers na nuvem

Preencha no `.env` a chave de quem você quiser. Cada uma é opcional e gratuita:

- Groq — https://console.groq.com/keys
- OpenRouter — https://openrouter.ai/keys
- Cerebras — https://cloud.cerebras.ai
- Google AI Studio — https://aistudio.google.com/apikey

## Executar

```bash
.venv\Scripts\python.exe -m uvicorn app.main:app --port 8080
```

- Chat: http://localhost:8080
- Documentação interativa: http://localhost:8080/docs
- Status: http://localhost:8080/health

---

## Autenticação

Defina as chaves que seus programas usarão, separadas por vírgula:

```env
HAWK_API_KEYS=minha-chave-secreta,chave-do-app-flutter
```

Os clientes mandam `Authorization: Bearer minha-chave-secreta`.

Se `HAWK_API_KEYS` ficar vazio, a autenticação fica **desligada** — aceitável só
em `localhost`. Antes de expor o servidor na VPS, preencha.

---

## Usando a API nos seus programas

Como o formato é o da OpenAI, use o SDK que você já conhece apontando a
`base_url` para o ai-hawk. Exemplos completos em [`examples/`](examples/).

### Python

```python
from openai import OpenAI

client = OpenAI(base_url="http://localhost:8080/v1", api_key="minha-chave-secreta")

resposta = client.chat.completions.create(
    model="ollama/llama3.2",           # <- troque aqui para mudar de LLM
    messages=[{"role": "user", "content": "Resuma o que e uma nota fiscal."}],
)
print(resposta.choices[0].message.content)
```

### C#

```csharp
using var http = new HttpClient { BaseAddress = new Uri("http://localhost:8080/") };
http.DefaultRequestHeaders.Authorization = new("Bearer", "minha-chave-secreta");

var body = new {
    model = "groq/llama-3.3-70b-versatile",
    messages = new[] { new { role = "user", content = "Ola" } }
};
var res = await http.PostAsJsonAsync("v1/chat/completions", body);
```

### Descobrir os modelos disponíveis

```bash
curl -H "Authorization: Bearer minha-chave-secreta" http://localhost:8080/v1/models
```

Cada item traz campos extras do ai-hawk além do padrão OpenAI:

```json
{
  "id": "groq/llama-3.3-70b-versatile",
  "label": "llama-3.3-70b-versatile",
  "provider": "groq",
  "tier": "free",
  "context_window": 131072
}
```

O `id` sempre tem a forma `provedor/modelo`. Se o nome do modelo for único no
catálogo, você também pode usar só ele (`llama3.2`).

---

## Endpoints

| Método | Rota | Descrição |
|---|---|---|
| `GET` | `/health` | Status, provedores ativos, estado do guarda de custo |
| `GET` | `/v1/models` | Catálogo unificado (`?refresh=true` força atualização) |
| `POST` | `/v1/chat/completions` | Conversa, com ou sem `stream` |
| `GET` | `/` | Interface de chat |
| `GET` | `/docs` | OpenAPI interativo |

### Erros

Formato OpenAI em todos os casos:

| Status | Quando |
|---|---|
| `401` | Chave ausente ou inválida |
| `402` | Modelo pago com o guarda de custo ligado |
| `404` | Modelo não existe no catálogo |
| `422` | Corpo da requisição inválido |
| `502/503/504` | Falha do provedor upstream (a mensagem diz qual) |

---

## Arquitetura

```
Seu programa  ─┐
               ├─→  ai-hawk  ─┬─→  Ollama (local)
Chat web      ─┘   (FastAPI)  ├─→  Groq / OpenRouter / Cerebras / Google
                              └─→  Anthropic / OpenAI   [bloqueados]
```

- `app/registry.py` — decide quais provedores existem e aplica o guarda de custo.
- `app/providers/openai_compat.py` — um adaptador só, para todos os provedores
  que falam o dialeto OpenAI (a maioria).
- `app/providers/anthropic_p.py` — adaptador nativo do Claude (protocolo próprio).

Para adicionar um provedor novo que fale o dialeto OpenAI, basta uma entrada em
`Registry._build`. Nenhum código novo.

---

## Desenvolvimento

```bash
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.venv\Scripts\python.exe -m pytest      # 17 testes, sem rede
.venv\Scripts\python.exe -m ruff check .
```

Os testes usam um provedor stub — rodam offline e não consomem nenhuma API.
