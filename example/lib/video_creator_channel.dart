import 'package:flutter/services.dart';

class VideoCreatorChannel {
  static const _channel = MethodChannel('video_creator');

  /// Creates an MP4 video at [videoPath] using audio from [audioPath],
  /// with [text] rendered on a dark background.
  static Future<void> createVideo({
    required String audioPath,
    required String videoPath,
    required String text,
  }) async {
    await _channel.invokeMethod('createVideo', {
      'audioPath': audioPath,
      'videoPath': videoPath,
      'text': text,
    });
  }
}
