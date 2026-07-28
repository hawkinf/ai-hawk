// Consumindo o ai-hawk a partir de um app Flutter/Dart.
//
// Dependencia: http (pubspec.yaml)
//   dependencies:
//     http: ^1.2.0

import 'dart:convert';
import 'dart:io';

import 'package:http/http.dart' as http;

class ModeloInfo {
  const ModeloInfo({
    required this.id,
    required this.label,
    required this.provider,
    required this.tier,
  });

  final String id;
  final String label;
  final String provider;
  final String tier;

  bool get gratuito => tier == 'free';

  factory ModeloInfo.fromJson(Map<String, dynamic> json) => ModeloInfo(
        id: json['id'] as String,
        label: json['label'] as String? ?? json['id'] as String,
        provider: json['provider'] as String? ?? 'desconhecido',
        tier: json['tier'] as String? ?? 'free',
      );
}

class AiHawkException implements Exception {
  AiHawkException(this.mensagem, this.statusCode);
  final String mensagem;
  final int statusCode;

  @override
  String toString() => 'AiHawkException($statusCode): $mensagem';
}

/// Cliente do servidor de IA da Hawk Informatica.
class AiHawkClient {
  AiHawkClient({
    this.baseUrl = 'http://localhost:8080/v1',
    this.apiKey,
    http.Client? client,
  }) : _client = client ?? http.Client();

  final String baseUrl;
  final String? apiKey;
  final http.Client _client;

  Map<String, String> get _headers => {
        'Content-Type': 'application/json',
        if (apiKey != null && apiKey!.isNotEmpty) 'Authorization': 'Bearer $apiKey',
      };

  Never _falhar(http.BaseResponse res, String corpo) {
    String mensagem = corpo;
    try {
      mensagem = (jsonDecode(corpo)['error']?['message'] as String?) ?? corpo;
    } catch (_) {}
    throw AiHawkException(mensagem, res.statusCode);
  }

  /// Lista as LLMs disponiveis. Use para popular o seletor de modelo na UI.
  Future<List<ModeloInfo>> listarModelos() async {
    final res = await _client.get(Uri.parse('$baseUrl/models'), headers: _headers);
    if (res.statusCode != 200) _falhar(res, res.body);

    final dados = jsonDecode(utf8.decode(res.bodyBytes))['data'] as List<dynamic>;
    return dados
        .map((m) => ModeloInfo.fromJson(m as Map<String, dynamic>))
        .toList(growable: false);
  }

  /// Envia a conversa e devolve a resposta completa.
  ///
  /// [modelo] escolhe a LLM, ex.: 'ollama/llama3.2' ou 'groq/llama-3.3-70b-versatile'.
  Future<String> perguntar({
    required String modelo,
    required List<Map<String, String>> mensagens,
  }) async {
    final res = await _client.post(
      Uri.parse('$baseUrl/chat/completions'),
      headers: _headers,
      body: jsonEncode({'model': modelo, 'messages': mensagens}),
    );
    if (res.statusCode != 200) _falhar(res, utf8.decode(res.bodyBytes));

    final corpo = jsonDecode(utf8.decode(res.bodyBytes));
    return corpo['choices'][0]['message']['content'] as String;
  }

  /// Mesma coisa, porem devolvendo os pedacos conforme chegam.
  Stream<String> perguntarStreaming({
    required String modelo,
    required List<Map<String, String>> mensagens,
  }) async* {
    final req = http.Request('POST', Uri.parse('$baseUrl/chat/completions'))
      ..headers.addAll(_headers)
      ..body = jsonEncode({
        'model': modelo,
        'messages': mensagens,
        'stream': true,
      });

    final res = await _client.send(req);
    if (res.statusCode != 200) {
      _falhar(res, await res.stream.bytesToString());
    }

    final linhas = res.stream.transform(utf8.decoder).transform(const LineSplitter());

    await for (final linha in linhas) {
      if (!linha.startsWith('data:')) continue;

      final dados = linha.substring(5).trim();
      if (dados == '[DONE]') return;

      final chunk = jsonDecode(dados) as Map<String, dynamic>;
      if (chunk.containsKey('error')) {
        throw AiHawkException(chunk['error']['message'] as String, 200);
      }

      final delta = chunk['choices'][0]['delta'] as Map<String, dynamic>;
      final conteudo = delta['content'] as String?;
      if (conteudo != null && conteudo.isNotEmpty) yield conteudo;
    }
  }

  void dispose() => _client.close();
}

Future<void> main() async {
  final client = AiHawkClient();

  final modelos = await client.listarModelos();
  if (modelos.isEmpty) {
    print('Nenhum modelo disponivel. Suba o Ollama ou o mock_provider.py.');
    return;
  }

  for (final m in modelos) {
    print('${m.id.padRight(45)} ${m.tier}');
  }

  // Trocar de LLM = trocar esta string.
  final escolhido = modelos.first.id;
  print('\nUsando: $escolhido\n');

  final conversa = [
    {'role': 'user', 'content': 'Explique o que e uma obrigacao acessoria.'}
  ];

  await for (final pedaco
      in client.perguntarStreaming(modelo: escolhido, mensagens: conversa)) {
    stdout.write(pedaco);
  }
  print('');

  client.dispose();
}
