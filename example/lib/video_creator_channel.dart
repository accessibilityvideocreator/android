import 'package:flutter/services.dart';

class VideoCreatorChannel {
  static const _channel = MethodChannel('video_creator');

  /// Creates an MP4 video using audio from [audioPath], with [text] rendered
  /// on a dark background. Returns the public file path where the video was saved
  /// (e.g. "Movies/TTS Videos/tts_video_123.mp4").
  static Future<String?> createVideo({
    required String audioPath,
    required String videoPath,
    required String text,
  }) async {
    final result = await _channel.invokeMethod<String>('createVideo', {
      'audioPath': audioPath,
      'videoPath': videoPath,
      'text': text,
    });
    return result;
  }

  /// Copies an audio file to Downloads/TTS Audio/ via MediaStore.
  static Future<void> saveAudioToDownloads(String audioPath) async {
    await _channel.invokeMethod('saveAudioToDownloads', {
      'audioPath': audioPath,
    });
  }
}
