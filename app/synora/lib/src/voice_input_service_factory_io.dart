import 'package:http/http.dart' as http;

import 'voice_input_service_base.dart';
import 'voice_input_service_io.dart';

VoiceInputService createPlatformVoiceInputService({http.Client? httpClient}) {
  return VoiceInputServiceImpl(httpClient: httpClient);
}
