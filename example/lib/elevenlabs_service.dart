import 'dart:convert';
import 'dart:io';
import 'package:flutter/foundation.dart';
import 'package:http/http.dart' as http;

class ElevenLabsService {
  static const String _baseUrl = 'https://api.elevenlabs.io/v1';

  // Default voice: "Rachel" — clear, neutral US English
  static const String defaultVoiceId = '21m00Tcm4TlvDq8ikWAM';

  final String apiKey;
  final String voiceId;

  ElevenLabsService({
    required this.apiKey,
    this.voiceId = defaultVoiceId,
  });

  /// Synthesizes [text] to an MP3 file at [outputPath].
  /// Throws on HTTP error or file I/O failure.
  Future<void> synthesizeToFile(String text, String outputPath) async {
    final uri = Uri.parse('$_baseUrl/text-to-speech/$voiceId');

    final response = await http.post(
      uri,
      headers: {
        'xi-api-key': apiKey,
        'Content-Type': 'application/json',
        'Accept': 'audio/mpeg',
      },
      body: jsonEncode({
        'text': text,
        'model_id': 'eleven_multilingual_v2',
        'voice_settings': {
          'stability': 0.5,
          'similarity_boost': 0.75,
        },
      }),
    );

    if (response.statusCode == 200) {
      final file = File(outputPath);
      await file.writeAsBytes(response.bodyBytes);
      if (kDebugMode) debugPrint('ElevenLabs audio saved to: $outputPath');
    } else {
      final body = utf8.decode(response.bodyBytes);
      throw Exception(
          'ElevenLabs API error ${response.statusCode}: $body');
    }
  }

  /// Fetches the list of available voices for this API key.
  Future<List<Map<String, dynamic>>> fetchVoices() async {
    final response = await http.get(
      Uri.parse('$_baseUrl/voices'),
      headers: {'xi-api-key': apiKey},
    );
    if (response.statusCode == 200) {
      final data = jsonDecode(response.body) as Map<String, dynamic>;
      return List<Map<String, dynamic>>.from(data['voices'] as List);
    } else {
      throw Exception(
          'Failed to fetch voices: ${response.statusCode}');
    }
  }
}
