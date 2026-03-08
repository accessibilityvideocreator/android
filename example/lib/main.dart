import 'dart:async';
import 'dart:io' show Platform, File, FileMode;
import 'dart:typed_data';
import 'dart:math';
import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter_tts/flutter_tts.dart';
import 'package:path_provider/path_provider.dart';
import 'package:open_filex/open_filex.dart';
import 'video_creator_channel.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'language_helper.dart'; // Import the facade
import 'elevenlabs_service.dart';

void main() => runApp(const MyApp());

class MyApp extends StatefulWidget {
  const MyApp({super.key});

  @override
  MyAppState createState() => MyAppState();
}

enum TtsState { playing, stopped, paused, continued }

class MyAppState extends State<MyApp> {
  late FlutterTts flutterTts;
  List<String?> rawEngines = [];
  List<DropdownMenuItem<String?>> engineItems = [];
  String? engine;
  List<Map<String, String>?> rawVoices = [];
  List<DropdownMenuItem<Map<String, String>?>> voiceItems = [];
  Completer<void> _voiceDataReadyCompleter = Completer<void>();
  Map<String, String>? voice;
  bool getDefaultVoiceRetried = false;
  List<String?> rawLanguages = [];
  List<DropdownMenuItem<String?>> languageItems = [];
  String? language;
  double volume = 0.8;
  double pitch = 1.0;
  double rate = !kIsWeb ? 0.5 : 0.9;
  bool isCurrentLanguageInstalled = false;

  String? _newVoiceText;
  int? _inputLength;

  bool _isCreatingVideo = false;
  String? _videoFilePath;

  final GlobalKey<NavigatorState> _navigatorKey = GlobalKey<NavigatorState>();

  // ElevenLabs settings
  String _elApiKey = '';
  String _elVoiceId = ElevenLabsService.defaultVoiceId;
  bool _useElevenLabs = true; // toggle off to use free device TTS while testing

  TtsState ttsState = TtsState.stopped;

  bool get isPlaying => ttsState == TtsState.playing;
  bool get isStopped => ttsState == TtsState.stopped;
  bool get isPaused => ttsState == TtsState.paused;
  bool get isContinued => ttsState == TtsState.continued;

  bool get isAndroid => !kIsWeb && Platform.isAndroid;
  bool get isIOS => !kIsWeb && Platform.isIOS;
  bool get isMacOS => !kIsWeb && Platform.isMacOS;
  bool get isWindows => !kIsWeb && Platform.isWindows;
  bool get isWeb => kIsWeb;

  @override
  initState() {
    super.initState();
    initTts();
    WidgetsBinding.instance.addPostFrameCallback((_) {
      _getDefaults(); // invoked after initial build of context is complete
    });
    _loadElSettings();
  }

  Future<void> _loadElSettings() async {
    final prefs = await SharedPreferences.getInstance();
    setState(() {
      _elApiKey        = prefs.getString('el_api_key') ?? '';
      _elVoiceId       = prefs.getString('el_voice_id') ?? ElevenLabsService.defaultVoiceId;
      _useElevenLabs   = prefs.getBool('el_enabled') ?? true;
    });
  }

  Future<void> _saveElSettings(String apiKey, String voiceId, bool useEl) async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString('el_api_key', apiKey);
    await prefs.setString('el_voice_id', voiceId);
    await prefs.setBool('el_enabled', useEl);
    setState(() {
      _elApiKey      = apiKey;
      _elVoiceId     = voiceId;
      _useElevenLabs = useEl;
    });
  }

  // from initState()
  void initTts() {
    flutterTts = FlutterTts();

    _setAwaitOptions();

    flutterTts.setStartHandler(() {
      setState(() {
        if (kDebugMode) debugPrint("TtsState: Playing");
        ttsState = TtsState.playing;
      });
    });

    flutterTts.setCompletionHandler(() {
      setState(() {
        if (kDebugMode) debugPrint("TtsState: Complete");
        ttsState = TtsState.stopped;
      });
    });

    flutterTts.setCancelHandler(() {
      setState(() {
        if (kDebugMode) debugPrint("TtsState: Cancel");
        ttsState = TtsState.stopped;
      });
    });

    flutterTts.setPauseHandler(() {
      setState(() {
        if (kDebugMode) debugPrint("TtsState: Paused");
        ttsState = TtsState.paused;
      });
    });

    flutterTts.setContinueHandler(() {
      setState(() {
        if (kDebugMode) debugPrint("TtsState: Continued");
        ttsState = TtsState.continued;
      });
    });

    flutterTts.setErrorHandler((msg) {
      setState(() {
        if (kDebugMode) debugPrint("TtsState: Error: $msg");
        ttsState = TtsState.stopped;
      });
    });
  }

  Future<void> _getDefaults() async {
    if (kDebugMode) debugPrint('_getDefaults...');
    if (isAndroid) await _getDefaultEngine();
    if (kIsWeb) setState(() {}); // Tickle the UI
    await _getDefaultVoice();
  }

  Future<dynamic> _getEngines() async => await flutterTts.getEngines;

  Future<dynamic> _getVoices() async => await flutterTts.getVoices;

  Future<dynamic> _getLanguages() async => await flutterTts.getLanguages;

  Future<void> _getDefaultEngine() async {
    if (!isAndroid) return; // safety-check
    if (kDebugMode) debugPrint('_getDefaultEngine...');
    var e = await flutterTts.getDefaultEngine;
    if (e != null) {
      if (kDebugMode) debugPrint('Default Engine: $e');
      setState(() => engine = e as String);
    }
  }

  Future<void> _getDefaultVoice() async {
    if (kDebugMode) debugPrint('_getDefaultVoice..');
    try {
      await _voiceDataReadyCompleter.future.timeout(const Duration(seconds: 2));
    } on TimeoutException {
      if (kDebugMode) debugPrint("Timeout waiting for voice data");
      if (!getDefaultVoiceRetried) {
        getDefaultVoiceRetried = true; // run only once
        _voiceDataReadyCompleter = Completer<void>(); // re-use
        setState(() {}); // Tickle the UI
        _getDefaultVoice();
      }
      return;
    } catch (e) {
      if (kDebugMode) debugPrint("Error waiting for voice data: $e");
      return;
    }
    if (kDebugMode) {
      debugPrint('_voiceDataReadyCompleter.isCompleted, so continuing..');
    }
    if (kDebugMode) debugPrint("rawVoices count: ${rawVoices.length}");
    if (rawVoices.isEmpty) return;

    if (isAndroid) {
      var defVoice = await flutterTts.getDefaultVoice;
      if (kDebugMode) debugPrint('Android Default Voice: $defVoice');
      if (defVoice != null) {
        var rawVoice = rawVoices.firstWhere((v) => mapEquals(v, defVoice));
        voice = rawVoice;
        if (voice != null) changedVoicesDropDownItem(voice);
      }
    } else {
      String myLocale;
      // Web may return just the language code, e.g. "de", if the browser's
      // Settings/Language contains preferred language entries containing only
      // the language without a region (e.g. "German" and not "German (Germany)").
      Locale deviceLocale = WidgetsBinding.instance.platformDispatcher.locale;
      if (kDebugMode) debugPrint('Device Locale (ISO): $deviceLocale');
      // TTS uses Unicode BCP47 Locale Identifiers instead of the ISO standard
      myLocale = deviceLocale.toLanguageTag();
      if (kIsWeb && !myLocale.contains('-')) {
        var webLocale = getBrowserLanguage();
        if (kDebugMode) debugPrint('webLocale: $webLocale');
        if (webLocale != null) myLocale = webLocale;
      }
      if (kDebugMode) debugPrint('Device/Browser Locale (BCP47): $myLocale');
      // TTS auto-selects the first matching raw voice with locale
      var rawVoice = rawVoices.firstWhere((v) => v?['locale'] == myLocale,
          orElse: () => rawVoices
              .firstWhere((v) => v?['locale']?.startsWith(myLocale) ?? false));
      voice = rawVoice;
      if (kDebugMode) debugPrint('Computed Default Voice: $voice');
      if (voice != null) changedVoicesDropDownItem(voice);
    }
  }

  Future<void> _speak() async {
    await flutterTts.setVolume(volume);
    await flutterTts.setSpeechRate(rate);
    await flutterTts.setPitch(pitch);

    if (_newVoiceText != null) {
      if (_newVoiceText!.isNotEmpty) {
        await flutterTts.speak(_newVoiceText!);
      }
    }
  }

  Future<void> _setAwaitOptions() async {
    await flutterTts.awaitSpeakCompletion(true);
  }

  Future<void> _stop() async {
    var result = await flutterTts.stop();
    if (result == 1) setState(() => ttsState = TtsState.stopped);
  }

  Future<void> _pause() async {
    var result = await flutterTts.pause();
    if (result == 1) setState(() => ttsState = TtsState.paused);
  }

  @override
  void dispose() {
    super.dispose();
    flutterTts.stop();
  }

  List<DropdownMenuItem<String?>> getEnginesDropDownMenuItems(
      List<dynamic> engines) {
    if (kDebugMode) debugPrint('getEnginesDropDownMenuItems...');
    if (engineItems.isEmpty) {
      rawEngines.clear();
      for (dynamic item in engines) {
        // if (kDebugMode) debugPrint('Engine: $item');
        rawEngines.add(item);
        engineItems
            .add(DropdownMenuItem<String?>(value: item, child: Text(item)));
      }
    }
    return engineItems;
  }

  Future<void> changedEnginesDropDownItem(String? selectedEngine) async {
    if (selectedEngine == null || selectedEngine.trim().isEmpty) {
      return;
    }
    if (kDebugMode) debugPrint('changedEnginesDropDownItem...');
    await flutterTts.setEngine(selectedEngine);
    engine = selectedEngine;
    voiceItems.clear();
    voice = null;
    languageItems.clear();
    language = null;
    isCurrentLanguageInstalled = false;
    _voiceDataReadyCompleter = Completer<void>(); // re-use
    setState(() {});
    getDefaultVoiceRetried = false;
    await _getDefaultVoice();
  }

  List<DropdownMenuItem<Map<String, String>?>> getVoicesDropDownMenuItems(
      List<dynamic> voices) {
    if (kDebugMode) {
      debugPrint('getVoicesDropDownMenuItems: voices count: ${voices.length}');
    }
    if (kDebugMode) debugPrint("voiceItems.count: ${voiceItems.length}");
    if (voiceItems.isEmpty) {
      rawVoices.clear();
      for (dynamic item in voices) {
        // if (kDebugMode) debugPrint('Voice: $item');
        var v = Map<String, String>.from(item);
        rawVoices.add(v); // remains unsorted
        // if (kDebugMode) debugPrint('Raw Voice: $v');
        var menuItem = DropdownMenuItem<Map<String, String>?>(
          value: v,
          child: Text("${v['name']} (${v['locale']})"),
        );
        if (!voiceItems
            .any((element) => mapEquals(element.value, menuItem.value))) {
          voiceItems.add(menuItem);
        }
      }
      voiceItems.sort((a, b) {
        return a.child
            .toString()
            .toLowerCase()
            .compareTo(b.child.toString().toLowerCase());
      });
    }
    if (voiceItems.isNotEmpty && !_voiceDataReadyCompleter.isCompleted) {
      _voiceDataReadyCompleter.complete();
      if (kDebugMode) {
        debugPrint(
            '_voiceDataReadyCompleter completed with ${voiceItems.length} voiceItems');
      }
    }
    return voiceItems;
  }

  Future<void> changedVoicesDropDownItem(
      Map<String, String>? selectedVoice) async {
    if (selectedVoice == null || selectedVoice.isEmpty) {
      return;
    }
    if (kDebugMode) debugPrint('changedVoicesDropDownItem...');
    await flutterTts.setVoice(selectedVoice);
    voice = selectedVoice;
    language = selectedVoice['locale'];
    setState(() {});
  }

  List<DropdownMenuItem<String?>> getLanguagesDropDownMenuItems(
      List<dynamic> languages) {
    if (kDebugMode) debugPrint('getLanguagesDropDownMenuItems...');
    if (languageItems.isEmpty) {
      rawLanguages.clear();
      for (dynamic item in languages) {
        // if (kDebugMode) debugPrint('Language: $item');
        rawLanguages.add(item); // remains unsorted
        var menuItem =
            DropdownMenuItem<String?>(value: item, child: Text(item));
        if (!languageItems.any((element) => element.value == menuItem.value)) {
          languageItems.add(menuItem);
        }
      }
      languageItems.sort((a, b) {
        return a.child
            .toString()
            .toLowerCase()
            .compareTo(b.child.toString().toLowerCase());
      });
    }
    return languageItems;
  }

  Future<void> changedLanguagesDropDownItem(String? selectedLanguage) async {
    if (selectedLanguage == null || selectedLanguage.trim().isEmpty) {
      return;
    }
    if (kDebugMode) debugPrint('changedLanguagesDropDownItem...');
    await flutterTts.setLanguage(selectedLanguage);
    language = selectedLanguage;
    if (isAndroid) {
      flutterTts
          .isLanguageInstalled(language!)
          .then((value) => isCurrentLanguageInstalled = (value as bool));
    } else {
      isCurrentLanguageInstalled = false;
    }

    // if the locale is changed, TTS auto-selects the first matching voice
    if (voiceItems.isNotEmpty) {
      var voiceItem =
          voiceItems.firstWhere((v) => v.value?['locale'] == selectedLanguage);
      voice = voiceItem.value;
      if (voice != null) changedVoicesDropDownItem(voice);
    }
  }

  void _onChange(String text) {
    setState(() {
      _newVoiceText = text;
    });
  }

  Future<void> _openElSettings() async {
    final keyCtrl   = TextEditingController(text: _elApiKey);
    final voiceCtrl = TextEditingController(text: _elVoiceId);
    bool  sheetUseEl = _useElevenLabs;

    await showModalBottomSheet(
      context: _navigatorKey.currentContext!,
      isScrollControlled: true,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(16)),
      ),
      builder: (ctx) => StatefulBuilder(
        builder: (ctx, setSheetState) => SingleChildScrollView(
          padding: EdgeInsets.only(
            left: 24, right: 24, top: 24,
            bottom: MediaQuery.of(ctx).viewInsets.bottom + 64, // +40px extra clearance
          ),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              const Text('ElevenLabs Settings',
                  style: TextStyle(fontSize: 18, fontWeight: FontWeight.bold)),
              const SizedBox(height: 4),
              const Text(
                'Get your API key at elevenlabs.io → Profile → API Keys',
                style: TextStyle(fontSize: 12, color: Colors.grey),
              ),
              const SizedBox(height: 16),
              // ── Use ElevenLabs toggle ──────────────────────────────────
              Container(
                decoration: BoxDecoration(
                  color: sheetUseEl
                      ? Colors.green.withOpacity(0.08)
                      : Colors.orange.withOpacity(0.08),
                  borderRadius: BorderRadius.circular(12),
                  border: Border.all(
                    color: sheetUseEl ? Colors.green : Colors.orange,
                    width: 1.2,
                  ),
                ),
                child: SwitchListTile(
                  title: Text(
                    sheetUseEl ? 'Using ElevenLabs' : 'Using Free Device TTS',
                    style: TextStyle(
                      fontWeight: FontWeight.w600,
                      color: sheetUseEl ? Colors.green : Colors.orange,
                    ),
                  ),
                  subtitle: Text(
                    sheetUseEl
                        ? 'High-quality voice (uses credits)'
                        : 'Free built-in voice — great for testing',
                    style: const TextStyle(fontSize: 12),
                  ),
                  value: sheetUseEl,
                  activeColor: Colors.green,
                  inactiveThumbColor: Colors.orange,
                  inactiveTrackColor: Colors.orange.withOpacity(0.3),
                  onChanged: (val) => setSheetState(() => sheetUseEl = val),
                ),
              ),
              const SizedBox(height: 16),
              TextField(
                controller: keyCtrl,
                obscureText: true,
                enabled: sheetUseEl,
                decoration: const InputDecoration(
                  labelText: 'API Key',
                  border: OutlineInputBorder(),
                  prefixIcon: Icon(Icons.key),
                ),
              ),
              const SizedBox(height: 12),
              TextField(
                controller: voiceCtrl,
                enabled: sheetUseEl,
                decoration: InputDecoration(
                  labelText: 'Voice ID',
                  border: const OutlineInputBorder(),
                  prefixIcon: const Icon(Icons.record_voice_over),
                  helperText: 'Default: Rachel (${ElevenLabsService.defaultVoiceId})',
                ),
              ),
              const SizedBox(height: 16),
              SizedBox(
                width: double.infinity,
                child: ElevatedButton(
                  onPressed: () {
                    // Strip invisible/non-ASCII chars that break HTTP headers
                    final key   = keyCtrl.text.replaceAll(RegExp(r'[^\x20-\x7E]'), '').trim();
                    final voice = voiceCtrl.text.trim().isEmpty
                        ? ElevenLabsService.defaultVoiceId
                        : voiceCtrl.text.trim();
                    _saveElSettings(key, voice, sheetUseEl);
                    Navigator.pop(ctx);
                    if (!sheetUseEl) {
                      _showSnack('Using free device TTS for testing');
                    } else if (key.isEmpty) {
                      _showSnack('API key cleared — using device TTS');
                    } else {
                      _showSnack('ElevenLabs enabled ✓');
                    }
                  },
                  child: const Text('Save'),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }

  void _showSnack(String msg) {
    final ctx = _navigatorKey.currentContext;
    if (ctx == null) return;
    ScaffoldMessenger.of(ctx)
        .showSnackBar(SnackBar(content: Text(msg)));
  }

  /// Builds a valid PCM WAV file filled with silence (all zeros).
  Uint8List _buildSilentWav(int durationSeconds, {int sampleRate = 22050}) {
    const channels    = 1;
    const bitsPerSample = 16;
    final numSamples  = sampleRate * durationSeconds;
    final dataSize    = numSamples * channels * (bitsPerSample ~/ 8);
    final buf         = ByteData(44 + dataSize);

    void str(int off, String s) {
      for (var i = 0; i < s.length; i++) buf.setUint8(off + i, s.codeUnitAt(i));
    }

    str(0,  'RIFF');
    buf.setUint32(4,  36 + dataSize, Endian.little);
    str(8,  'WAVE');
    str(12, 'fmt ');
    buf.setUint32(16, 16, Endian.little);           // fmt chunk size
    buf.setUint16(20, 1,  Endian.little);           // PCM
    buf.setUint16(22, channels, Endian.little);
    buf.setUint32(24, sampleRate, Endian.little);
    buf.setUint32(28, sampleRate * channels * (bitsPerSample ~/ 8), Endian.little);
    buf.setUint16(32, channels * (bitsPerSample ~/ 8), Endian.little);
    buf.setUint16(34, bitsPerSample, Endian.little);
    str(36, 'data');
    buf.setUint32(40, dataSize, Endian.little);
    // bytes 44..end are already zero = silence
    return buf.buffer.asUint8List();
  }

  /// Appends an error to a persistent log file and opens it automatically.
  Future<void> _logAndOpenError(String context, dynamic error, [StackTrace? stack]) async {
    // Show snack immediately so user sees something even if file ops fail
    _showSnack('Error: $error');

    try {
      final extDir = await getExternalStorageDirectory()
          ?? await getTemporaryDirectory();
      final logFile = File('${extDir.path}/tts_error_log.txt');
      final timestamp = DateTime.now().toIso8601String();
      final entry = '[$timestamp]\nContext: $context\nError: $error'
          '${stack != null ? '\nStack:\n$stack' : ''}\n${'─' * 60}\n';
      await logFile.writeAsString(entry, mode: FileMode.append);
      debugPrint('Error log written to: ${logFile.path}');
      await Future.delayed(const Duration(seconds: 1)); // let snack show first
      await OpenFilex.open(logFile.path);
    } catch (e) {
      debugPrint('Failed to write error log: $e');
    }
  }

  String _wrapText(String text, int maxWidth) {
    final words = text.trim().split(RegExp(r'\s+'));
    final lines = <String>[];
    var line = '';
    for (final w in words) {
      if (line.isEmpty) {
        line = w;
      } else if (line.length + 1 + w.length <= maxWidth) {
        line += ' $w';
      } else {
        lines.add(line);
        line = w;
      }
    }
    if (line.isNotEmpty) lines.add(line);
    if (lines.length > 10) {
      return '${lines.take(9).join('\n')}\n...';
    }
    return lines.join('\n');
  }

Future<void> _createVideo() async {
    if (_newVoiceText == null || _newVoiceText!.trim().isEmpty) return;

    setState(() {
      _isCreatingVideo = true;
      _videoFilePath = null;
    });

    try {
      // Step 1: Synthesize TTS to an audio file
      // Use external storage — device TTS engine runs in a separate process
      // and cannot write to the app's private cache directory.
      final extDir = await getExternalStorageDirectory();
      final storageDir = extDir ?? await getTemporaryDirectory();

      String audioPath;
      File audioFile;

      if (_elApiKey.isNotEmpty && _useElevenLabs) {
        // ElevenLabs writes the file itself — use external storage, MP3 format
        audioPath = '${storageDir.path}/tts_audio.mp3';
        audioFile = File(audioPath);
        if (await audioFile.exists()) await audioFile.delete();

        _showSnack('Generating audio with ElevenLabs...');
        debugPrint('Calling ElevenLabs API... path: $audioPath');
        final el = ElevenLabsService(apiKey: _elApiKey, voiceId: _elVoiceId);
        await el.synthesizeToFile(_newVoiceText!, audioPath);
        debugPrint('ElevenLabs done. size: ${await audioFile.length()}');
      } else {
        // Free testing mode — generate a silent WAV locally (no TTS engine needed).
        // Device TTS synthesizeToFile is unreliable across Android vendors/versions.
        // Cap at 30 s while debugging so video encodes quickly.
        const maxTestSeconds = 30;
        final wordCount = (_newVoiceText ?? '').trim().split(RegExp(r'\s+')).length;
        // Rough estimate: ~120 words per minute at default speech rate
        final estimatedSecs = ((wordCount / 120.0) * 60).round().clamp(3, maxTestSeconds);

        audioPath = '${storageDir.path}/tts_audio.wav';
        audioFile = File(audioPath);
        _showSnack('Generating silent test audio ($estimatedSecs s)...');
        debugPrint('Writing silent WAV: $audioPath  duration=${estimatedSecs}s');
        await audioFile.writeAsBytes(_buildSilentWav(estimatedSecs));
      }

      // Step 2: Create output path
      _showSnack('Audio ready — preparing video output path...');
      final timestamp = DateTime.now().millisecondsSinceEpoch;
      final videoPath = '${storageDir.path}/tts_$timestamp.mp4';

      // Step 3: Create video using native Android MediaMuxer
      _showSnack('Encoding video (this may take a while for long texts)...');
      final publicPath = await VideoCreatorChannel.createVideo(
        audioPath: audioPath,
        videoPath: videoPath,
        text: _newVoiceText!,
      );

      // publicPath is either "Movies/TTS Videos/filename.mp4" (friendly display)
      // or the original temp path as fallback
      final openPath = (publicPath != null && publicPath.startsWith('/'))
          ? publicPath   // absolute temp path fallback
          : videoPath;   // open the temp path; MediaStore has the real copy

      setState(() => _videoFilePath = publicPath ?? videoPath);
      _showSnack('Saved to ${publicPath ?? "storage"}  — opening...');
      await flutterTts.speak('Your video is ready');
      await OpenFilex.open(openPath);
    } catch (e, stack) {
      if (kDebugMode) debugPrint('_createVideo error: $e\n$stack');
      await _logAndOpenError('_createVideo', e, stack);
    } finally {
      setState(() => _isCreatingVideo = false);
    }
  }

  /// Copies the last generated ElevenLabs audio file to Downloads/TTS Audio/
  /// so it's easy to find. Falls back gracefully if the source doesn't exist.
  Future<void> saveToFile(String? text) async {
    if (!_elApiKey.isNotEmpty || !_useElevenLabs) {
      _showSnack('Enable ElevenLabs to save audio');
      return;
    }
    try {
      final extDir = await getExternalStorageDirectory();
      final storageDir = extDir ?? await getTemporaryDirectory();
      final audioSrc = File('${storageDir.path}/tts_audio.mp3');
      if (!await audioSrc.exists()) {
        _showSnack('No audio found — create a video first');
        return;
      }

      // Copy to Downloads/TTS Audio/ using MediaStore channel
      await VideoCreatorChannel.saveAudioToDownloads(audioSrc.path);
      _showSnack('Audio saved to Downloads/TTS Audio/');
    } catch (e, stack) {
      await _logAndOpenError('saveToFile', e, stack);
    }
  }

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      navigatorKey: _navigatorKey,
      home: Scaffold(
        appBar: AppBar(
          title: const Text('Copy Paste, Type, Or Dictate Text Below',
              style: TextStyle(fontSize: 13)),
          actions: [
            IconButton(
              icon: Stack(
                children: [
                  const Icon(Icons.settings),
                  if (_elApiKey.isNotEmpty)
                    Positioned(
                      right: 0,
                      top: 0,
                      child: Container(
                        width: 8,
                        height: 8,
                        decoration: BoxDecoration(
                          // green = ElevenLabs active, orange = key saved but disabled
                          color: _useElevenLabs ? Colors.green : Colors.orange,
                          shape: BoxShape.circle,
                        ),
                      ),
                    ),
                ],
              ),
              tooltip: 'ElevenLabs Settings',
              onPressed: _openElSettings,
            ),
          ],
        ),
        body: SingleChildScrollView(
          scrollDirection: Axis.vertical,
          child: Column(
            children: [
              _inputSection(),
              _btnSection(),
              if (isAndroid) _videoSection(),
              // Engine/voice/language selectors hidden — using ElevenLabs TTS
              // _engineSection(),
              // _voiceSection(),
              // _languageSection(),
              _buildSliders(),
              if (isAndroid) _getMaxSpeechInputLengthSection(),
            ],
          ),
        ),
        floatingActionButton: (isAndroid || isIOS) &&
                _newVoiceText != null &&
                _newVoiceText!.trim().isNotEmpty
            ? FloatingActionButton(
                mini: true,
                onPressed: () => saveToFile(_newVoiceText),
                tooltip: 'Synthesize to File',
                child: const Icon(Icons.save),
              )
            : null,
      ),
    );
  }

  Widget _videoSection() {
    return Container(
      padding: const EdgeInsets.symmetric(vertical: 16.0, horizontal: 24.0),
      child: Column(
        children: [
          ElevatedButton.icon(
            icon: _isCreatingVideo
                ? const SizedBox(
                    width: 18,
                    height: 18,
                    child: CircularProgressIndicator(
                        strokeWidth: 2, color: Colors.white),
                  )
                : const Icon(Icons.videocam),
            label: Text(
                _isCreatingVideo ? 'Creating Video...' : 'Create Video'),
            onPressed: (_isCreatingVideo ||
                    _newVoiceText == null ||
                    _newVoiceText!.trim().isEmpty)
                ? null
                : _createVideo,
            style: ElevatedButton.styleFrom(
              backgroundColor: Colors.deepPurple,
              foregroundColor: Colors.white,
              padding: const EdgeInsets.symmetric(
                  horizontal: 28, vertical: 12),
            ),
          ),
          if (_videoFilePath != null)
            Padding(
              padding: const EdgeInsets.only(top: 8.0),
              child: Text(
                '✓ Saved: $_videoFilePath',
                style: const TextStyle(fontSize: 11, color: Colors.green),
                textAlign: TextAlign.center,
              ),
            ),
        ],
      ),
    );
  }

  Widget _engineSection() {
    if (isAndroid) {
      if (engineItems.isNotEmpty) {
        return _enginesDropDownSection(<dynamic>[]);
      } else {
        return FutureBuilder<dynamic>(
            future: _getEngines(),
            builder: (BuildContext context, AsyncSnapshot<dynamic> snapshot) {
              if (snapshot.connectionState == ConnectionState.done) {
                if (snapshot.hasData) {
                  return _enginesDropDownSection(
                      snapshot.data as List<dynamic>);
                } else if (snapshot.hasError) {
                  return Text('Error: ${snapshot.error}');
                } else {
                  return const Text('No data to load engines');
                }
              } else if (snapshot.connectionState == ConnectionState.waiting) {
                return const Text('Loading engines...');
              } else {
                // Other states (e.g., ConnectionState.none,
                // or if future is null initially)
                return const Text('Waiting to start loading engines...');
              }
            });
      }
    } else {
      return const SizedBox(width: 0, height: 0);
    }
  }

  Widget _voiceSection() {
    if (voiceItems.isNotEmpty) {
      if (!_voiceDataReadyCompleter.isCompleted) {
        _voiceDataReadyCompleter.complete(); // Safety complete
      }
      return _voicesDropDownSection(<dynamic>[]);
    } else {
      return FutureBuilder<dynamic>(
          future: _getVoices(),
          builder: (BuildContext context, AsyncSnapshot<dynamic> snapshot) {
            if (snapshot.connectionState == ConnectionState.done) {
              if (snapshot.hasData) {
                return _voicesDropDownSection(snapshot.data as List<dynamic>);
              } else if (snapshot.hasError) {
                if (!_voiceDataReadyCompleter.isCompleted) {
                  _voiceDataReadyCompleter.completeError(
                      snapshot.error ?? "Unknown error loading voices");
                }
                return Text('Error: ${snapshot.error}');
              } else {
                return const Text('No data to load voices');
              }
            } else if (snapshot.connectionState == ConnectionState.waiting) {
              return const Text('Loading voices...');
            } else {
              // Other states (e.g., ConnectionState.none,
              // or if future is null initially)
              return const Text('Waiting to start loading voices...');
            }
          });
    }
  }

  Widget _languageSection() {
    if (languageItems.isNotEmpty) {
      return _languageDropDownSection(<dynamic>[]);
    } else {
      return FutureBuilder<dynamic>(
          future: _getLanguages(),
          builder: (BuildContext context, AsyncSnapshot<dynamic> snapshot) {
            if (snapshot.connectionState == ConnectionState.done) {
              if (snapshot.hasData) {
                return _languageDropDownSection(snapshot.data as List<dynamic>);
              } else if (snapshot.hasError) {
                return Text('Error: ${snapshot.error}');
              } else {
                return const Text('No data to load languages');
              }
            } else if (snapshot.connectionState == ConnectionState.waiting) {
              return const Text('Loading Languages...');
            } else {
              // Other states (e.g., ConnectionState.none,
              // or if future is null initially)
              return const Text('Waiting to start loading languages...');
            }
          });
    }
  }

  Widget _inputSection() => Container(
      alignment: Alignment.topCenter,
      padding: const EdgeInsets.only(top: 25.0, left: 25.0, right: 25.0),
      child: TextField(
        maxLines: 11,
        minLines: 6,
        onChanged: (String value) {
          _onChange(value);
        },
      ));

  Widget _btnSection() {
    return Container(
      padding: const EdgeInsets.only(top: 50.0),
      child: Row(
        mainAxisAlignment: MainAxisAlignment.spaceEvenly,
        children: [
          _buildButtonColumn(Colors.green, Colors.greenAccent, Icons.play_arrow,
              'PLAY', _speak),
          _buildButtonColumn(
              Colors.red, Colors.redAccent, Icons.stop, 'STOP', _stop),
          _buildButtonColumn(
              Colors.blue, Colors.blueAccent, Icons.pause, 'PAUSE', _pause),
        ],
      ),
    );
  }

  Widget _enginesDropDownSection(List<dynamic> engines) {
    return Container(
      padding: const EdgeInsets.only(top: 50.0),
      child: DropdownButton<String?>(
        value: engine,
        hint: const Text('Choose an engine'),
        items: getEnginesDropDownMenuItems(engines),
        onChanged: changedEnginesDropDownItem,
      ),
    );
  }

  Widget _voicesDropDownSection(List<dynamic> voices) {
    return Container(
      padding: const EdgeInsets.only(top: 10.0),
      child: DropdownButton<Map<String, String>?>(
        value: voice,
        hint: const Text('Choose a voice'),
        items: getVoicesDropDownMenuItems(voices),
        onChanged: changedVoicesDropDownItem,
      ),
    );
  }

  Widget _languageDropDownSection(List<dynamic> languages) {
    return Container(
        padding: const EdgeInsets.only(top: 10.0),
        child: Row(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            DropdownButton<String?>(
              value: language,
              hint: const Text('Choose a language'),
              items: getLanguagesDropDownMenuItems(languages),
              onChanged: changedLanguagesDropDownItem,
            ),
            const SizedBox(
              width: 5.0,
            ),
            Visibility(
              visible: isAndroid,
              child: Text("Is installed: $isCurrentLanguageInstalled"),
            ),
          ],
        ));
  }

  Column _buildButtonColumn(Color color, Color splashColor, IconData icon,
      String label, Function func) {
    return Column(
        mainAxisSize: MainAxisSize.min,
        mainAxisAlignment: MainAxisAlignment.center,
        children: [
          IconButton(
              icon: Icon(icon),
              color: color,
              splashColor: splashColor,
              onPressed: () => func()),
          Container(
              margin: const EdgeInsets.only(top: 8.0),
              child: Text(label,
                  style: TextStyle(
                      fontSize: 12.0,
                      fontWeight: FontWeight.w400,
                      color: color)))
        ]);
  }

  Widget _getMaxSpeechInputLengthSection() {
    return Row(
      mainAxisAlignment: MainAxisAlignment.spaceEvenly,
      children: [
        ElevatedButton(
          child: const Text('Get max speech input length'),
          onPressed: () async {
            _inputLength = await flutterTts.getMaxSpeechInputLength;
            setState(() {});
          },
        ),
        Text("$_inputLength characters"),
      ],
    );
  }

  Widget _buildSliders() {
    return Column(
      children: [_volume(), _pitch(), _rate()],
    );
  }

  Widget _volume() {
    return Slider(
        value: volume,
        onChanged: (newVolume) {
          setState(() => volume = newVolume);
        },
        min: 0.0,
        max: 1.0,
        divisions: 10,
        label: "Volume: $volume");
  }

  Widget _pitch() {
    return Slider(
      value: pitch,
      onChanged: (newPitch) {
        setState(() => pitch = newPitch);
      },
      min: 0.5,
      max: 2.0,
      divisions: 15,
      label: "Pitch: $pitch",
      activeColor: Colors.red,
    );
  }

  Widget _rate() {
    return Slider(
      value: rate,
      onChanged: (newRate) {
        setState(() => rate = newRate);
      },
      min: 0.0,
      max: 1.0,
      divisions: 10,
      label: "Rate: $rate",
      activeColor: Colors.green,
    );
  }
}
