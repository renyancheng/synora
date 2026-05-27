import 'package:flutter/foundation.dart';
import 'package:http/http.dart' as http;

import 'voice_input_service_factory.dart';

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

abstract class VoiceInputService {
  Future<bool> get isSupported;

  Future<bool> hasModel();

  Future<void> ensureReady({ValueChanged<double>? onDownloadProgress});

  Future<void> cancelModelDownload();

  Future<void> startListening();

  Future<VoiceInputResult> stopListening();

  Future<void> cancelListening();

  Future<void> dispose();
}

VoiceInputService createVoiceInputService({http.Client? httpClient}) {
  return createPlatformVoiceInputService(httpClient: httpClient);
}
