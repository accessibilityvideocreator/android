import 'dart:convert';
import 'dart:io';
import 'package:http/http.dart' as http;

class GoogleTtsService {
  final String apiKey;
  final String voiceName;
  final String languageCode;

  static const defaultVoiceName    = 'en-US-Standard-B';
  static const defaultLanguageCode = 'en-US';

  GoogleTtsService({
    required String apiKey,
    this.voiceName    = defaultVoiceName,
    this.languageCode = defaultLanguageCode,
  }) : apiKey = _sanitize(apiKey);

  static String _sanitize(String key) =>
      key.replaceAll(RegExp(r'[^\x20-\x7E]'), '').trim();

  Future<void> synthesizeToFile(String text, String outputPath) async {
    final url = Uri.parse(
      'https://texttospeech.googleapis.com/v1/text:synthesize?key=$apiKey',
    );

    final response = await http.post(
      url,
      headers: {'Content-Type': 'application/json'},
      body: jsonEncode({
        'input': {'text': text},
        'voice': {
          'languageCode': languageCode,
          'name': voiceName,
        },
        'audioConfig': {
          'audioEncoding': 'MP3',
        },
      }),
    ).timeout(const Duration(minutes: 2));

    if (response.statusCode != 200) {
      throw Exception(
          'Google TTS error ${response.statusCode}: ${response.body}');
    }

    final data = jsonDecode(response.body) as Map<String, dynamic>;
    final audioContent = data['audioContent'] as String?;
    if (audioContent == null || audioContent.isEmpty) {
      throw Exception('Google TTS returned empty audio content');
    }

    final audioBytes = base64Decode(audioContent);
    if (audioBytes.isEmpty) {
      throw Exception('Google TTS audio decoded to 0 bytes');
    }

    await File(outputPath).writeAsBytes(audioBytes);
  }
}
