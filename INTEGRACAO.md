# Integrando seus programas ao ai-hawk

Guia prático para consumir o servidor de IA a partir dos seus programas.

> Todos os trechos deste guia foram executados contra o servidor de produção
> (`192.168.200.5:8081`) e funcionam como estão.

A API é **compatível com a da OpenAI**. Se seu programa já usa um SDK da OpenAI,
basta trocar a `base_url` e a chave — nada mais muda.

---

## 1. Endereço e chave

| Onde | URL base |
|---|---|
| Rede local | `http://192.168.200.5:8081/v1` |
| Internet | `https://ia.hawk.com.br/ai-hawk/v1` |

Autenticação em toda requisição:

```
Authorization: Bearer <sua-chave>
```

A chave está em `/opt/ai-hawk-server/.env`, campo `HAWK_API_KEYS`:

```bash
ssh -o RemoteCommand=none -o RequestTTY=no ai-hawk 'grep HAWK_API_KEYS /opt/ai-hawk-server/.env'
```

> **Nunca coloque a chave no código.** Use variável de ambiente ou o cofre de
> credenciais da sua plataforma. Se vazar, gere outra no `.env` e reinicie:
> `sudo systemctl restart ai-hawk-server`.

Para separar seus programas, use uma chave por aplicação (lista por vírgula):

```env
HAWK_API_KEYS=chave-erp,chave-app-flutter,chave-scripts
```

---

## 2. Escolhendo o modelo

O campo `model` decide qual LLM responde. Liste o que está disponível:

```bash
curl -H "Authorization: Bearer $CHAVE" http://192.168.200.5:8081/v1/models
```

Modelos atuais (todos rodam local, custo zero):

| `model` | Bom para |
|---|---|
| `hawk/gemma4` | uso geral, raciocínio — **é o que fica carregado por padrão** |
| `hawk/qwen3coder` | código |
| `hawk/gemma3ab` | uso geral, sem restrições de conteúdo |
| `hawk/qwen38` | 27B de raciocínio — mais capaz, porém **~7 tokens/s** (não cabe inteiro na GPU) |
| `hawk/qwen3.6-35b:latest` | tarefas mais pesadas |
| `hawk/laguna-xs-2.1:latest` | uso geral |
| `hawk/huihui_ai/qwen3-abliterated:14b` | uso geral, sem restrições |
| `hawk/huihui_ai/qwen3-abliterated:30b-a3b-thinking-2507-q4_K_M` | raciocínio longo |
| `hawk/hf.co/mradermacher/Foundation-Sec-8B-Instruct-GGUF:Q4_K_M` | segurança da informação |
| `hawk/hf.co/bartowski/WhiteRabbitNeo_WhiteRabbitNeo-V3-7B-GGUF:Q4_K_M` | segurança ofensiva |
| `litellm/bge-m3` | **embeddings** (busca semântica), não conversa |

Os ids têm a forma `provedor/modelo`. Se o nome for único no catálogo, o nome
sozinho também funciona (`gemma4`).

---

## 3. As três regras que evitam 90% dos problemas

### Regra 1 — `max_tokens` alto (mínimo 2000)

`gemma4` e os modelos `*-thinking` **raciocinam antes de responder**, e o
raciocínio consome o orçamento de saída. Com `max_tokens` baixo, todo o
orçamento vai para o raciocínio e **o texto final volta vazio**, com
`finish_reason: "length"`.

```jsonc
{ "model": "hawk/gemma4", "max_tokens": 4000 }   // ok
{ "model": "hawk/gemma4", "max_tokens": 50 }     // devolve string vazia
```

### Regra 2 — timeout de 300 segundos

Só cabe **um modelo grande por vez** na GPU (RTX 3060, 12 GB). Se seu programa
pedir um modelo diferente do que está carregado, o servidor troca — e a troca
leva **cerca de 2 minutos**. Requisições normais respondem em segundos; a
primeira após uma troca, não.

Use timeout de **300s**. Um timeout de 30s vai falhar exatamente quando o
usuário trocar de modelo.

### Regra 3 — trate o `503` como "tente de novo"

Se outro modelo grande estiver em uso, a resposta é `503` com a mensagem
*"outra pessoa está usando a GPU"*. **Não é erro do seu programa** — é a fila da
GPU. Repita depois de alguns segundos.

> **Dica para evitar as regras 2 e 3:** se seus programas puderem padronizar em
> **um único modelo** (`hawk/gemma4`, que já é o padrão carregado), nunca haverá
> troca e as respostas serão sempre rápidas.

---

## 4. Códigos de erro

| Código | Significado | O que seu programa deve fazer |
|---|---|---|
| `200` | ok | — |
| `401` | chave ausente ou errada | conferir o header `Authorization` |
| `402` | modelo pago com a trava de custo ligada | usar um modelo gratuito |
| `404` | o `model` não existe | conferir com `GET /v1/models` |
| `422` | corpo da requisição inválido | conferir o JSON |
| `503` | **GPU ocupada** ou backend fora do ar | **repetir com espera** |
| `504` | demorou demais | repetir |

O corpo do erro sempre tem o mesmo formato:

```json
{ "error": { "message": "...", "type": "provider_error", "code": "hawk" } }
```

### Repetição recomendada

Repita **apenas** em `503` e `504`, com espera crescente. Não repita `4xx` —
esses são erros do seu lado e vão falhar de novo.

```
tentativa 1 -> espera 5s
tentativa 2 -> espera 15s
tentativa 3 -> espera 30s
desiste
```

---

## 5. Python

```bash
pip install openai
```

```python
import os
from openai import OpenAI

cliente = OpenAI(
    base_url="http://192.168.200.5:8081/v1",
    api_key=os.environ["AI_HAWK_KEY"],
    timeout=300.0,     # a troca de modelo na GPU leva ~2 min
    max_retries=3,
)

resposta = cliente.chat.completions.create(
    model="hawk/gemma4",
    messages=[
        {"role": "system", "content": "Responda em português, de forma objetiva."},
        {"role": "user", "content": "Resuma o que e uma nota fiscal eletronica."},
    ],
    max_tokens=4000,   # modelos de raciocínio precisam de espaço
)
print(resposta.choices[0].message.content)
```

### Streaming (mostra a resposta enquanto é gerada)

```python
fluxo = cliente.chat.completions.create(
    model="hawk/gemma4",
    messages=[{"role": "user", "content": "Explique o Simples Nacional."}],
    max_tokens=4000,
    stream=True,
)
for chunk in fluxo:
    pedaco = chunk.choices[0].delta.content
    if pedaco:
        print(pedaco, end="", flush=True)
```

### Sem SDK, só `httpx`

Veja o exemplo completo e comentado em
[`examples/cliente_python.py`](examples/cliente_python.py).

---

## 6. C#

Sem pacote NuGet — o formato é o da OpenAI, então `HttpClient` basta.
Cliente completo, com streaming, em
[`examples/ClienteCSharp.cs`](examples/ClienteCSharp.cs).

```csharp
using System.Net.Http.Json;
using System.Text.Json;

var http = new HttpClient
{
    BaseAddress = new Uri("http://192.168.200.5:8081/"),
    Timeout = TimeSpan.FromMinutes(5),   // troca de modelo na GPU
};
http.DefaultRequestHeaders.Authorization =
    new("Bearer", Environment.GetEnvironmentVariable("AI_HAWK_KEY"));

var corpo = new
{
    model = "hawk/gemma4",
    messages = new[] { new { role = "user", content = "Explique o que e um CNPJ." } },
    max_tokens = 4000,
};

using var res = await http.PostAsJsonAsync("v1/chat/completions", corpo);
res.EnsureSuccessStatusCode();

using var doc = JsonDocument.Parse(await res.Content.ReadAsStringAsync());
var texto = doc.RootElement
    .GetProperty("choices")[0]
    .GetProperty("message")
    .GetProperty("content")
    .GetString();
```

---

## 7. Flutter / Dart

```yaml
# pubspec.yaml
dependencies:
  http: ^1.2.0
```

Cliente completo, com streaming e tratamento de erro, em
[`examples/cliente_dart.dart`](examples/cliente_dart.dart).

```dart
final res = await http.post(
  Uri.parse('http://192.168.200.5:8081/v1/chat/completions'),
  headers: {
    'Content-Type': 'application/json',
    'Authorization': 'Bearer ${const String.fromEnvironment('AI_HAWK_KEY')}',
  },
  body: jsonEncode({
    'model': 'hawk/gemma4',
    'messages': [
      {'role': 'user', 'content': 'Explique o que e uma obrigacao acessoria.'}
    ],
    'max_tokens': 4000,
  }),
);

final texto = jsonDecode(utf8.decode(res.bodyBytes))['choices'][0]['message']['content'];
```

> Em Flutter, o timeout padrão do `http` é curto demais. Use
> `.timeout(const Duration(minutes: 5))` na chamada.

---

## 8. Conversa com histórico

A API **não guarda estado**. Para o modelo lembrar do contexto, mande a
conversa inteira a cada requisição:

```python
historico = [{"role": "system", "content": "Voce e um assistente fiscal."}]

def perguntar(texto: str) -> str:
    historico.append({"role": "user", "content": texto})
    r = cliente.chat.completions.create(
        model="hawk/gemma4", messages=historico, max_tokens=4000
    )
    resposta = r.choices[0].message.content
    historico.append({"role": "assistant", "content": resposta})
    return resposta
```

O contexto é grande (256 mil tokens no `gemma4`), mas conversas muito longas
ficam lentas e caras em tempo de GPU. Corte o histórico antigo quando passar de
algumas dezenas de mensagens.

---

## 9. Antes de colocar em produção

- [ ] Chave em variável de ambiente, **nunca** no código nem no repositório
- [ ] Uma chave por programa, para poder revogar individualmente
- [ ] Timeout de 300s configurado no cliente
- [ ] `max_tokens` ≥ 2000
- [ ] Repetição só em `503`/`504`, com espera crescente
- [ ] Mensagem amigável ao usuário quando der `503` ("IA ocupada, tentando de novo")
- [ ] Se for usar de fora da rede local, apontar para `https://ia.hawk.com.br/ai-hawk/v1`

### Testar a conexão

```bash
curl -H "Authorization: Bearer $CHAVE" \
     -H "Content-Type: application/json" \
     -d '{"model":"hawk/gemma4","messages":[{"role":"user","content":"diga ok"}],"max_tokens":2000}' \
     --max-time 300 \
     http://192.168.200.5:8081/v1/chat/completions
```

Se isso responder, seu programa também vai responder.

---

## 10. Diagnóstico rápido

| Sintoma | Causa provável |
|---|---|
| `401` | chave errada, ou header sem o prefixo `Bearer ` |
| Resposta vazia | `max_tokens` baixo demais (ver Regra 1) |
| Timeout do cliente | timeout curto durante troca de modelo (ver Regra 2) |
| `503` constante | outro modelo carregado na GPU (ver Regra 3) |
| `404` no `model` | id errado — confira em `GET /v1/models` |
| Acentos errados | leia o corpo como UTF-8 (`utf8.decode` no Dart) |

Estado do servidor a qualquer momento:

```bash
curl http://192.168.200.5:8081/health
```

Casos mais profundos estão em
[`.claude/DEBUG_PLAYBOOK.md`](.claude/DEBUG_PLAYBOOK.md).
