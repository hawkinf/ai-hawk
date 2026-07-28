// Consumindo o ai-hawk a partir de um programa C# (.NET 8+).
//
// Nao precisa de pacote NuGet: o formato e o da OpenAI, entao HttpClient basta.
// Se preferir o SDK oficial, use OpenAI (NuGet) apontando a Endpoint para
// http://localhost:8080/v1 - funciona igual.

using System.Net.Http.Json;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace Hawk.AiClient;

public sealed record Mensagem(
    [property: JsonPropertyName("role")] string Role,
    [property: JsonPropertyName("content")] string Content);

public sealed record ModeloInfo(
    [property: JsonPropertyName("id")] string Id,
    [property: JsonPropertyName("label")] string Label,
    [property: JsonPropertyName("provider")] string Provider,
    [property: JsonPropertyName("tier")] string Tier);

/// <summary>Cliente do servidor de IA da Hawk Informatica.</summary>
public sealed class AiHawkClient : IDisposable
{
    private readonly HttpClient _http;

    public AiHawkClient(string baseUrl = "http://localhost:8080/", string? apiKey = null)
    {
        _http = new HttpClient
        {
            BaseAddress = new Uri(baseUrl),
            Timeout = TimeSpan.FromMinutes(5),
        };
        if (!string.IsNullOrWhiteSpace(apiKey))
        {
            _http.DefaultRequestHeaders.Authorization =
                new System.Net.Http.Headers.AuthenticationHeaderValue("Bearer", apiKey);
        }
    }

    /// <summary>Lista as LLMs disponiveis no servidor.</summary>
    public async Task<IReadOnlyList<ModeloInfo>> ListarModelosAsync(
        CancellationToken ct = default)
    {
        using var res = await _http.GetAsync("v1/models", ct);
        res.EnsureSuccessStatusCode();

        using var doc = JsonDocument.Parse(await res.Content.ReadAsStringAsync(ct));
        return doc.RootElement.GetProperty("data")
            .Deserialize<List<ModeloInfo>>() ?? [];
    }

    /// <summary>Envia uma conversa e devolve a resposta completa.</summary>
    /// <param name="modelo">Id da LLM, ex.: "ollama/llama3.2" ou "groq/llama-3.3-70b-versatile".</param>
    public async Task<string> PerguntarAsync(
        string modelo,
        IEnumerable<Mensagem> mensagens,
        CancellationToken ct = default)
    {
        var payload = new { model = modelo, messages = mensagens };
        using var res = await _http.PostAsJsonAsync("v1/chat/completions", payload, ct);

        if ((int)res.StatusCode == 402)
        {
            var erro = await res.Content.ReadAsStringAsync(ct);
            throw new InvalidOperationException($"Guarda de custo do ai-hawk: {erro}");
        }
        res.EnsureSuccessStatusCode();

        using var doc = JsonDocument.Parse(await res.Content.ReadAsStringAsync(ct));
        return doc.RootElement
            .GetProperty("choices")[0]
            .GetProperty("message")
            .GetProperty("content")
            .GetString() ?? string.Empty;
    }

    /// <summary>Envia uma conversa e devolve os pedacos conforme chegam.</summary>
    public async IAsyncEnumerable<string> PerguntarStreamingAsync(
        string modelo,
        IEnumerable<Mensagem> mensagens,
        [System.Runtime.CompilerServices.EnumeratorCancellation] CancellationToken ct = default)
    {
        var payload = new { model = modelo, messages = mensagens, stream = true };
        using var req = new HttpRequestMessage(HttpMethod.Post, "v1/chat/completions")
        {
            Content = JsonContent.Create(payload),
        };

        using var res = await _http.SendAsync(req, HttpCompletionOption.ResponseHeadersRead, ct);
        res.EnsureSuccessStatusCode();

        await using var stream = await res.Content.ReadAsStreamAsync(ct);
        using var reader = new StreamReader(stream);

        while (await reader.ReadLineAsync(ct) is { } linha)
        {
            if (!linha.StartsWith("data:", StringComparison.Ordinal)) continue;

            var dados = linha[5..].Trim();
            if (dados == "[DONE]") yield break;

            using var doc = JsonDocument.Parse(dados);
            if (doc.RootElement.TryGetProperty("error", out var erro))
            {
                throw new InvalidOperationException(
                    erro.GetProperty("message").GetString());
            }

            var delta = doc.RootElement.GetProperty("choices")[0].GetProperty("delta");
            if (delta.TryGetProperty("content", out var conteudo))
            {
                var texto = conteudo.GetString();
                if (!string.IsNullOrEmpty(texto)) yield return texto;
            }
        }
    }

    public void Dispose() => _http.Dispose();
}

internal static class Exemplo
{
    public static async Task Main()
    {
        using var client = new AiHawkClient(apiKey: null); // preencha se HAWK_API_KEYS estiver setado

        var modelos = await client.ListarModelosAsync();
        if (modelos.Count == 0)
        {
            Console.WriteLine("Nenhum modelo disponivel. Suba o Ollama ou o mock_provider.py.");
            return;
        }

        foreach (var m in modelos)
            Console.WriteLine($"{m.Id,-45} {m.Tier}");

        // Trocar de LLM = trocar esta string.
        var escolhido = modelos[0].Id;
        Console.WriteLine($"\nUsando: {escolhido}\n");

        var conversa = new[] { new Mensagem("user", "Explique o que e um CNPJ em uma frase.") };

        await foreach (var pedaco in client.PerguntarStreamingAsync(escolhido, conversa))
            Console.Write(pedaco);

        Console.WriteLine();
    }
}
