import 'dart:async';
import 'dart:io';

import 'package:archive/archive.dart';
import 'package:flutter/foundation.dart';
import 'package:http/http.dart' as http;
import 'package:path/path.dart' as path;
import 'package:path_provider/path_provider.dart';
import 'package:record/record.dart';
import 'package:sherpa_onnx/sherpa_onnx.dart' as sherpa_onnx;

class VoiceInputException implements Exception {
  VoiceInputException(this.code, this.message);

  final String code;
  final String message;

  @override
  String toString() => message;
}

class VoiceInputResult {
  const VoiceInputResult(this.text);

  final String text;
}

class VoiceInputService {
  VoiceInputService({http.Client? httpClient}) : _httpClient = httpClient ?? http.Client();

  static const String _modelVersion = 'sherpa-onnx-streaming-zipformer-bilingual-zh-en-2023-02-20';
  static const String _downloadUrl =
      'https://gh-proxy.org/https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-streaming-zipformer-bilingual-zh-en-2023-02-20.tar.bz2';
  static const int _sampleRate = 16000;

  final http.Client _httpClient;
  final AudioRecorder _audioRecorder = AudioRecorder();

  sherpa_onnx.OnlineRecognizer? _recognizer;
  sherpa_onnx.OnlineStream? _stream;
  StreamSubscription<Uint8List>? _audioSubscription;
  String _lastText = '';
  bool _initialized = false;

  Future<bool> get isSupported async => !kIsWeb && defaultTargetPlatform == TargetPlatform.android;

  Future<void> ensureReady({ValueChanged<double>? onDownloadProgress}) async {
    if (!await isSupported) {
      throw VoiceInputException('unsupported_device', '语音输入首版仅支持 Android。');
    }
    final modelDir = await _ensureModelReady(onDownloadProgress: onDownloadProgress);
    if (_initialized && _recognizer != null) {
      return;
    }
    try {
      sherpa_onnx.initBindings();
      final config = sherpa_onnx.OnlineRecognizerConfig(
        model: sherpa_onnx.OnlineModelConfig(
          transducer: sherpa_onnx.OnlineTransducerModelConfig(
            encoder: path.join(modelDir.path, 'encoder-epoch-99-avg-1.int8.onnx'),
            decoder: path.join(modelDir.path, 'decoder-epoch-99-avg-1.onnx'),
            joiner: path.join(modelDir.path, 'joiner-epoch-99-avg-1.onnx'),
          ),
          tokens: path.join(modelDir.path, 'tokens.txt'),
          modelType: 'zipformer',
        ),
        ruleFsts: '',
      );
      _recognizer = sherpa_onnx.OnlineRecognizer(config);
      _initialized = true;
    } catch (_) {
      throw VoiceInputException('init_failed', '语音识别初始化失败，请稍后重试。');
    }
  }

  Future<void> startListening() async {
    if (!await _audioRecorder.hasPermission()) {
      throw VoiceInputException('permission_denied', '未获得麦克风权限，请先开启后再试。');
    }
    if (!await _audioRecorder.isEncoderSupported(AudioEncoder.pcm16bits)) {
      throw VoiceInputException('unsupported_device', '当前设备暂不支持这组录音参数。');
    }
    final recognizer = _recognizer;
    if (recognizer == null) {
      throw VoiceInputException('init_failed', '语音识别初始化失败，请稍后重试。');
    }
    _stream?.free();
    _stream = recognizer.createStream();
    _lastText = '';
    try {
      final audioStream = await _audioRecorder.startStream(
        const RecordConfig(
          encoder: AudioEncoder.pcm16bits,
          sampleRate: _sampleRate,
          numChannels: 1,
        ),
      );
      _audioSubscription = audioStream.listen(_consumeAudioChunk);
    } catch (_) {
      throw VoiceInputException('unsupported_device', '当前设备暂不支持这组录音参数。');
    }
  }

  Future<VoiceInputResult> stopListening() async {
    await _audioSubscription?.cancel();
    _audioSubscription = null;
    await _audioRecorder.stop();
    final recognizer = _recognizer;
    final stream = _stream;
    if (recognizer == null || stream == null) {
      throw VoiceInputException('init_failed', '语音识别初始化失败，请稍后重试。');
    }
    stream.inputFinished();
    while (recognizer.isReady(stream)) {
      recognizer.decode(stream);
    }
    final text = _normalize(_lastText.isNotEmpty ? _lastText : recognizer.getResult(stream).text);
    if (text.isEmpty) {
      throw VoiceInputException('empty_result', '没有识别到清晰的语音内容，请再试一次。');
    }
    return VoiceInputResult(text);
  }

  Future<void> cancelListening() async {
    await _audioSubscription?.cancel();
    _audioSubscription = null;
    try {
      await _audioRecorder.stop();
    } catch (_) {}
    _stream?.free();
    _stream = null;
    _lastText = '';
  }

  Future<void> dispose() async {
    await cancelListening();
    _audioRecorder.dispose();
    _recognizer?.free();
  }

  void _consumeAudioChunk(Uint8List data) {
    final recognizer = _recognizer;
    final stream = _stream;
    if (recognizer == null || stream == null) {
      return;
    }
    stream.acceptWaveform(samples: _pcm16ToFloat32(data), sampleRate: _sampleRate);
    while (recognizer.isReady(stream)) {
      recognizer.decode(stream);
    }
    final text = recognizer.getResult(stream).text.trim();
    if (text.isNotEmpty) {
      _lastText = text;
    }
  }

  Float32List _pcm16ToFloat32(Uint8List bytes) {
    final values = Float32List(bytes.length ~/ 2);
    final data = ByteData.view(bytes.buffer);
    for (var index = 0; index < bytes.length; index += 2) {
      values[index ~/ 2] = data.getInt16(index, Endian.little) / 32768.0;
    }
    return values;
  }

  String _normalize(String input) {
    return input
        .split('\n')
        .map((item) => item.replaceFirst(RegExp(r'^\d+:\s*'), '').trim())
        .where((item) => item.isNotEmpty)
        .join('\n')
        .trim();
  }

  Future<Directory> _ensureModelReady({ValueChanged<double>? onDownloadProgress}) async {
    final root = await getApplicationSupportDirectory();
    final modelsRoot = Directory(path.join(root.path, 'asr_models'));
    final modelDir = Directory(path.join(modelsRoot.path, _modelVersion));
    if (await _isModelReady(modelDir)) {
      return modelDir;
    }
    await modelsRoot.create(recursive: true);
    final archiveFile = File(path.join(modelsRoot.path, '$_modelVersion.tar.bz2'));
    try {
      final response = await _httpClient.send(http.Request('GET', Uri.parse(_downloadUrl)));
      if (response.statusCode < 200 || response.statusCode >= 300) {
        throw VoiceInputException('download_failed', '语音模型下载失败，请检查网络后重试。');
      }
      final sink = archiveFile.openWrite();
      final total = response.contentLength ?? 0;
      var received = 0;
      await for (final chunk in response.stream) {
        sink.add(chunk);
        received += chunk.length;
        if (total > 0 && onDownloadProgress != null) {
          onDownloadProgress(received / total);
        }
      }
      await sink.close();
      final compressed = await archiveFile.readAsBytes();
      final tarBytes = BZip2Decoder().decodeBytes(compressed);
      final archive = TarDecoder().decodeBytes(tarBytes);
      for (final file in archive.files) {
        final outputPath = path.join(modelsRoot.path, file.name);
        if (file.isFile) {
          final out = File(outputPath);
          await out.parent.create(recursive: true);
          await out.writeAsBytes(file.content as List<int>, flush: true);
        } else {
          await Directory(outputPath).create(recursive: true);
        }
      }
      if (!await _isModelReady(modelDir)) {
        throw VoiceInputException('download_failed', '语音模型下载失败，请检查网络后重试。');
      }
      return modelDir;
    } catch (error) {
      if (error is VoiceInputException) {
        rethrow;
      }
      throw VoiceInputException('download_failed', '语音模型下载失败，请检查网络后重试。');
    } finally {
      if (await archiveFile.exists()) {
        await archiveFile.delete();
      }
    }
  }

  Future<bool> _isModelReady(Directory modelDir) async {
    final files = <String>[
      'encoder-epoch-99-avg-1.int8.onnx',
      'decoder-epoch-99-avg-1.onnx',
      'joiner-epoch-99-avg-1.onnx',
      'tokens.txt',
    ];
    for (final name in files) {
      if (!await File(path.join(modelDir.path, name)).exists()) {
        return false;
      }
    }
    return true;
  }
}
