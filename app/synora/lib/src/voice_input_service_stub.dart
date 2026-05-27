import 'package:flutter/foundation.dart';
import 'package:http/http.dart' as http;

import 'voice_input_service_base.dart';

class VoiceInputServiceImpl implements VoiceInputService {
  VoiceInputServiceImpl({http.Client? httpClient});

  @override
  Future<bool> get isSupported async => false;

  @override
  Future<bool> hasModel() async => false;

  @override
  Future<void> ensureReady({ValueChanged<double>? onDownloadProgress}) async {
    throw VoiceInputException('unsupported_device', '语音输入首版仅支持 Android。');
  }

  @override
  Future<void> cancelModelDownload() async {}

  @override
  Future<void> startListening() async {
    throw VoiceInputException('unsupported_device', '语音输入首版仅支持 Android。');
  }

  @override
  Future<VoiceInputResult> stopListening() async {
    throw VoiceInputException('unsupported_device', '语音输入首版仅支持 Android。');
  }

  @override
  Future<void> cancelListening() async {}

  @override
  Future<void> dispose() async {}
}
