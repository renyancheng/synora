import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:http/http.dart' as http;

import 'models.dart';

class ApiException implements Exception {
  ApiException(this.message);

  final String message;

  @override
  String toString() => message;
}

class ApiClient {
  ApiClient({String? baseUrl}) : _baseUrl = baseUrl ?? _defaultBaseUrl();

  final String _baseUrl;
  final http.Client _httpClient = http.Client();

  String? _accessToken;

  void setAccessToken(String? token) {
    _accessToken = token;
  }

  static String _defaultBaseUrl() {
    const configured = String.fromEnvironment('SYNORA_API_BASE_URL');
    if (configured.isNotEmpty) {
      return configured;
    }
    return kIsWeb ? 'http://localhost:8000' : 'http://10.0.2.2:8000';
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

  Future<UploadedAttachment> uploadAttachment(
    InputSourceType sourceType,
    LocalAttachmentData attachment,
  ) async {
    final request = http.MultipartRequest(
      'POST',
      Uri.parse('${_normalizedBase()}/attachments/upload'),
    );
    request.fields['source_type'] = sourceType.apiValue;
    request.headers.addAll(_authHeaders(includeJson: false));
    request.files.add(
      http.MultipartFile.fromBytes(
        'file',
        attachment.bytes,
        filename: attachment.fileName,
      ),
    );
    final response = await request.send();
    final body = await response.stream.bytesToString();
    final decoded = body.isEmpty ? null : jsonDecode(body);
    if (response.statusCode < 200 || response.statusCode >= 300) {
      throw ApiException(_extractErrorMessage(decoded, response.statusCode));
    }
    return UploadedAttachment.fromJson(decoded as Map<String, dynamic>);
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

  Future<ScheduleDraftResult> createScheduleDraft({
    required InputSourceType sourceType,
    required String textContent,
    required List<int> attachmentIds,
  }) async {
    final json = await _sendJson(
      'POST',
      '/schedule/drafts',
      body: {
        'source_type': sourceType.apiValue,
        'text_content': textContent,
        'attachment_ids': attachmentIds,
        'context': <String, String>{},
      },
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

  Future<QuickNoteDraftPreview> createQuickNoteDraft({
    required InputSourceType sourceType,
    required String content,
    required List<String> tags,
    required List<int> attachmentIds,
  }) async {
    final json = await _sendJson(
      'POST',
      '/quick-notes/drafts',
      body: {
        'source_type': sourceType.apiValue,
        'content': content,
        'tags': tags,
        'attachment_ids': attachmentIds,
        'context': <String, String>{},
      },
    );
    return QuickNoteDraftPreview.fromJson(json as Map<String, dynamic>);
  }

  Future<QuickNoteSaveResult> confirmQuickNote({
    required String content,
    required List<String> tags,
    required InputSourceType sourceType,
    required List<int> attachmentIds,
    required String approvalToken,
  }) async {
    final json = await _sendJson(
      'POST',
      '/quick-notes/confirm',
      body: {
        'content': content,
        'tags': tags,
        'source_type': sourceType.apiValue,
        'attachment_ids': attachmentIds,
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
    final uri = Uri.parse('${_normalizedBase()}$path');
    final request = http.Request(method, uri);
    request.headers.addAll(_authHeaders(includeJson: true, authenticated: authenticated));
    request.body = body == null ? '' : jsonEncode(body);
    final response = await _httpClient.send(request);
    final responseBody = await response.stream.bytesToString();
    final decoded = responseBody.isEmpty ? null : jsonDecode(responseBody);
    if (response.statusCode < 200 || response.statusCode >= 300) {
      throw ApiException(_extractErrorMessage(decoded, response.statusCode));
    }
    return decoded;
  }

  Map<String, String> _authHeaders({
    required bool includeJson,
    bool authenticated = true,
  }) {
    final headers = <String, String>{};
    if (includeJson) {
      headers['Content-Type'] = 'application/json';
    }
    if (authenticated) {
      final token = _accessToken;
      if (token == null || token.isEmpty) {
        throw ApiException('请先登录后再操作。');
      }
      headers['Authorization'] = 'Bearer $token';
    }
    return headers;
  }

  String _normalizedBase() {
    return _baseUrl.endsWith('/') ? _baseUrl.substring(0, _baseUrl.length - 1) : _baseUrl;
  }

  String _extractErrorMessage(dynamic decoded, int statusCode) {
    if (decoded is Map<String, dynamic> && decoded['detail'] is String) {
      return decoded['detail'] as String;
    }
    return '请求失败（$statusCode），请稍后重试。';
  }

  List<Map<String, dynamic>> _asList(dynamic json) {
    if (json is! List<dynamic>) {
      throw ApiException('返回数据格式不正确。');
    }
    return json.map((item) => item as Map<String, dynamic>).toList();
  }
}
