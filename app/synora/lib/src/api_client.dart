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

  Future<List<ConversationThreadItem>> fetchConversations() async {
    final json = await _sendJson('GET', '/agent/conversations');
    final items = ((json as Map<String, dynamic>)['items'] as List<dynamic>? ?? <dynamic>[])
        .map((item) => ConversationThreadItem.fromJson(item as Map<String, dynamic>))
        .toList();
    return items;
  }

  Future<ConversationThreadItem> createConversation({String? title}) async {
    final json = await _sendJson(
      'POST',
      '/agent/conversations',
      body: title == null ? <String, dynamic>{} : <String, dynamic>{'title': title},
    );
    return ConversationThreadItem.fromJson((json as Map<String, dynamic>)['conversation'] as Map<String, dynamic>);
  }

  Future<(ConversationThreadItem, List<ConversationMessageItem>)> fetchConversationMessages(int conversationId) async {
    final json = await _sendJson('GET', '/agent/conversations/$conversationId/messages');
    final map = json as Map<String, dynamic>;
    final conversation = ConversationThreadItem.fromJson(map['conversation'] as Map<String, dynamic>);
    final items = (map['items'] as List<dynamic>? ?? <dynamic>[])
        .map((item) => ConversationMessageItem.fromJson(item as Map<String, dynamic>))
        .toList();
    return (conversation, items);
  }

  Future<ConversationSendMessageResult> sendConversationMessage({
    required int conversationId,
    required String textContent,
    required InputSourceType sourceType,
    required List<int> attachmentIds,
    Map<String, String> context = const <String, String>{},
  }) async {
    final json = await _sendJson(
      'POST',
      '/agent/conversations/$conversationId/messages',
      body: {
        'text_content': textContent,
        'source_type': sourceType.apiValue,
        'attachment_ids': attachmentIds,
        'context': context,
      },
    );
    return ConversationSendMessageResult.fromJson(json as Map<String, dynamic>);
  }

  Future<ConversationActionResult> performConversationAction({
    required int conversationId,
    required String action,
    Map<String, dynamic> payload = const <String, dynamic>{},
  }) async {
    final json = await _sendJson(
      'POST',
      '/agent/conversations/$conversationId/actions',
      body: {
        'action': action,
        'payload': payload,
      },
    );
    return ConversationActionResult.fromJson(json as Map<String, dynamic>);
  }

  Future<void> deleteSchedule(int scheduleId) async {
    await _sendJson('DELETE', '/schedule/$scheduleId');
  }

  Future<void> deleteQuickNote(int noteId) async {
    await _sendJson('DELETE', '/quick-notes/$noteId');
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
        throw ApiException('请先登录。');
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
      final detail = (decoded['detail'] as String).trim();
      if (detail.isNotEmpty) {
        return detail;
      }
    }
    switch (statusCode) {
      case 401:
        return '登录已失效，请重新登录。';
      case 404:
        return '请求的内容不存在。';
      case 422:
        return '提交内容不完整，请检查后重试。';
      default:
        return '请求失败（$statusCode），请稍后重试。';
    }
  }

  List<Map<String, dynamic>> _asList(dynamic json) {
    if (json is! List<dynamic>) {
      throw ApiException('返回数据格式不正确。');
    }
    return json.map((item) => item as Map<String, dynamic>).toList();
  }
}
