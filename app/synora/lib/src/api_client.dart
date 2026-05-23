import 'dart:convert';
import 'dart:io';

import 'models.dart';

class ApiException implements Exception {
  ApiException(this.message);

  final String message;

  @override
  String toString() => message;
}

class ApiClient {
  ApiClient({String? baseUrl})
      : _baseUrl = baseUrl ??
            const String.fromEnvironment(
              'SYNORA_API_BASE_URL',
              defaultValue: 'http://10.0.2.2:8000',
            );

  final String _baseUrl;
  final HttpClient _httpClient = HttpClient();

  String? _accessToken;

  void setAccessToken(String? token) {
    _accessToken = token;
  }

  Future<SessionInfo> login(String email, String password) async {
    final json = await _sendJson(
      'POST',
      '/auth/login',
      body: {'email': email, 'password': password},
      authenticated: false,
    );
    final session = SessionInfo.fromJson(json as Map<String, dynamic>);
    setAccessToken(session.accessToken);
    return session;
  }

  Future<List<ScheduleItem>> fetchSchedules() async {
    final json = await _sendJson('GET', '/schedule');
    return _asList(json).map(ScheduleItem.fromJson).toList();
  }

  Future<List<QuickNoteItem>> fetchQuickNotes() async {
    final json = await _sendJson('GET', '/quick-notes');
    return _asList(json).map(QuickNoteItem.fromJson).toList();
  }

  Future<List<NotificationItem>> fetchNotifications() async {
    final json = await _sendJson('GET', '/notifications');
    return _asList(json).map(NotificationItem.fromJson).toList();
  }

  Future<ScheduleDraftResult> createScheduleDraft(String inputText) async {
    final json = await _sendJson(
      'POST',
      '/schedule/drafts',
      body: {'input_text': inputText, 'context': <String, String>{}},
    );
    return ScheduleDraftResult.fromJson(json as Map<String, dynamic>);
  }

  Future<ConflictCheckResult> checkScheduleConflicts(
    ScheduleDraft draft,
    String draftHash,
  ) async {
    final json = await _sendJson(
      'POST',
      '/schedule/conflicts',
      body: {'draft': draft.toJson(), 'draft_hash': draftHash},
    );
    return ConflictCheckResult.fromJson(json as Map<String, dynamic>);
  }

  Future<ScheduleConfirmResult> confirmSchedule(
    String approvalToken,
    ScheduleDraft draft,
  ) async {
    final json = await _sendJson(
      'POST',
      '/schedule/confirm',
      body: {
        'approval_token': approvalToken,
        'normalized_draft': draft.toJson(),
      },
    );
    return ScheduleConfirmResult.fromJson(json as Map<String, dynamic>);
  }

  Future<QuickNotePreview> previewQuickNote(
    String content,
    List<String> tags,
  ) async {
    final json = await _sendJson(
      'POST',
      '/quick-notes',
      body: {'content': content, 'tags': tags},
    );
    return QuickNotePreview.fromJson(json as Map<String, dynamic>);
  }

  Future<QuickNoteSaveResult> confirmQuickNote(
    String content,
    List<String> tags,
    String approvalToken,
  ) async {
    final json = await _sendJson(
      'POST',
      '/quick-notes',
      body: {
        'content': content,
        'tags': tags,
        'approval_token': approvalToken,
      },
    );
    return QuickNoteSaveResult.fromJson(json as Map<String, dynamic>);
  }

  Future<dynamic> _sendJson(
    String method,
    String path, {
    Map<String, dynamic>? body,
    bool authenticated = true,
  }) async {
    final normalizedBase =
        _baseUrl.endsWith('/') ? _baseUrl.substring(0, _baseUrl.length - 1) : _baseUrl;
    final request = await _httpClient.openUrl(
      method,
      Uri.parse('$normalizedBase$path'),
    );
    request.headers.contentType = ContentType.json;
    if (authenticated) {
      final token = _accessToken;
      if (token == null || token.isEmpty) {
        throw ApiException('Please log in first.');
      }
      request.headers.set(HttpHeaders.authorizationHeader, 'Bearer $token');
    }
    if (body != null) {
      request.write(jsonEncode(body));
    }

    final response = await request.close();
    final responseBody = await response.transform(utf8.decoder).join();
    final decoded = responseBody.isEmpty ? null : jsonDecode(responseBody);
    if (response.statusCode < 200 || response.statusCode >= 300) {
      if (decoded is Map<String, dynamic> && decoded['detail'] is String) {
        throw ApiException(decoded['detail'] as String);
      }
      throw ApiException('Request failed: ${response.statusCode}');
    }
    return decoded;
  }

  List<Map<String, dynamic>> _asList(dynamic json) {
    if (json is! List<dynamic>) {
      throw ApiException('Unexpected response payload.');
    }
    return json.map((item) => item as Map<String, dynamic>).toList();
  }
}
