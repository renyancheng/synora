import 'dart:async';
import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:http/http.dart' as http;

import 'models.dart';
import 'strings.dart';

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
    if (kIsWeb) {
      return 'http://localhost:8000';
    }
    // Android 模拟器经 10.0.2.2 访问宿主机；桌面端（Windows/macOS/Linux）直连 localhost
    if (defaultTargetPlatform == TargetPlatform.android) {
      return 'http://10.0.2.2:8000';
    }
    return 'http://localhost:8000';
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

  Future<SessionInfo> register({
    required String email,
    required String password,
    required String displayName,
  }) async {
    final json = await _sendJson(
      'POST',
      '/auth/register',
      body: {'email': email, 'password': password, 'display_name': displayName},
      authenticated: false,
    );
    final session = SessionInfo.fromJson(json as Map<String, dynamic>);
    setAccessToken(session.accessToken);
    return session;
  }

  Future<CurrentSessionInfo> fetchCurrentSession() async {
    final json = await _sendJson('GET', '/auth/me');
    return CurrentSessionInfo.fromJson(json as Map<String, dynamic>);
  }

  Future<UserPreferences> fetchUserPreferences() async {
    final json = await _sendJson('GET', '/users/me/preferences');
    return UserPreferences.fromJson(json as Map<String, dynamic>);
  }

  Future<UserPreferences> updateUserPreferences() async {
    final json = await _sendJson('PATCH', '/users/me/preferences');
    return UserPreferences.fromJson(json as Map<String, dynamic>);
  }

  Future<void> logout() async {
    try {
      await _sendJson('POST', '/auth/logout');
    } finally {
      setAccessToken(null);
    }
  }

  Future<UploadedAttachment> uploadAttachment(
    LocalAttachmentData attachment,
  ) async {
    try {
      final request = http.MultipartRequest(
        'POST',
        Uri.parse('${_normalizedBase()}/attachments/upload'),
      );
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
      final decoded = _decodeJsonBody(body);
      if (response.statusCode < 200 || response.statusCode >= 300) {
        throw ApiException(
          _extractErrorMessage(decoded, response.statusCode, body),
        );
      }
      return UploadedAttachment.fromJson(decoded as Map<String, dynamic>);
    } on http.ClientException catch (error) {
      throw _mapClientException(error);
    }
  }

  Future<List<ScheduleItem>> fetchSchedules({String? query}) async {
    final params = <String, String>{};
    final cleanedQuery = query?.trim() ?? '';
    if (cleanedQuery.isNotEmpty) {
      params['q'] = cleanedQuery;
    }
    final path = params.isEmpty
        ? '/schedule'
        : '/schedule?${Uri(queryParameters: params).query}';
    final json = await _sendJson('GET', path);
    return _asList(json).map(ScheduleItem.fromJson).toList();
  }

  Future<List<QuickNoteItem>> fetchQuickNotes({
    String? tag,
    String? query,
  }) async {
    final params = <String, String>{};
    final cleanedTag = tag?.trim() ?? '';
    final cleanedQuery = query?.trim() ?? '';
    if (cleanedTag.isNotEmpty) {
      params['tag'] = cleanedTag;
    }
    if (cleanedQuery.isNotEmpty) {
      params['q'] = cleanedQuery;
    }
    final path = params.isEmpty
        ? '/quick-notes'
        : '/quick-notes?${Uri(queryParameters: params).query}';
    final json = await _sendJson('GET', path);
    return _asList(json).map(QuickNoteItem.fromJson).toList();
  }

  Future<List<QuickNoteTagItem>> fetchQuickNoteTags() async {
    final json = await _sendJson('GET', '/quick-notes/tags');
    return _asList(json).map(QuickNoteTagItem.fromJson).toList();
  }

  Future<List<NotificationItem>> fetchNotifications() async {
    final json = await _sendJson('GET', '/notifications');
    return _asList(json).map(NotificationItem.fromJson).toList();
  }

  Future<void> registerDeviceToken(String token, String platform) async {
    await _sendJson('POST', '/devices/register', body: <String, dynamic>{
      'token': token,
      'platform': platform,
    });
  }

  Future<void> unregisterDeviceToken(String token) async {
    await _sendJson('POST', '/devices/unregister', body: <String, dynamic>{
      'token': token,
    });
  }

  Future<MemoryListResult> fetchMemory() async {
    final json = await _sendJson('GET', '/memory');
    return MemoryListResult.fromJson(json as Map<String, dynamic>);
  }

  Future<void> deleteMemory(int memoryId) async {
    await _sendJson('DELETE', '/memory/$memoryId');
  }

  Future<void> clearMemory() async {
    await _sendJson('POST', '/memory/clear');
  }

  Future<List<ConversationThreadItem>> fetchConversations() async {
    final json = await _sendJson('GET', '/agent/conversations');
    return ((json as Map<String, dynamic>)['items'] as List<dynamic>? ??
            <dynamic>[])
        .map(
          (item) =>
              ConversationThreadItem.fromJson(item as Map<String, dynamic>),
        )
        .toList();
  }

  Future<ConversationThreadItem> createConversation({String? title}) async {
    final json = await _sendJson(
      'POST',
      '/agent/conversations',
      body: title == null
          ? <String, dynamic>{}
          : <String, dynamic>{'title': title},
    );
    return ConversationThreadItem.fromJson(
      (json as Map<String, dynamic>)['conversation'] as Map<String, dynamic>,
    );
  }

  Future<(ConversationThreadItem, List<ConversationMessageItem>)>
  fetchConversationMessages(int conversationId) async {
    final json = await _sendJson(
      'GET',
      '/agent/conversations/$conversationId/messages',
    );
    final map = json as Map<String, dynamic>;
    final conversation = ConversationThreadItem.fromJson(
      map['conversation'] as Map<String, dynamic>,
    );
    final items = (map['items'] as List<dynamic>? ?? <dynamic>[])
        .map(
          (item) =>
              ConversationMessageItem.fromJson(item as Map<String, dynamic>),
        )
        .toList();
    return (conversation, items);
  }

  Future<ConversationThreadItem> renameConversation({
    required int conversationId,
    required String title,
  }) async {
    final json = await _sendJson(
      'PATCH',
      '/agent/conversations/$conversationId',
      body: <String, dynamic>{'title': title},
    );
    return ConversationThreadItem.fromJson(
      (json as Map<String, dynamic>)['conversation'] as Map<String, dynamic>,
    );
  }

  Future<void> deleteConversation(int conversationId) async {
    await _sendJson('DELETE', '/agent/conversations/$conversationId');
  }

  Future<ConversationRewindResult> rewindConversationLastTurn(
    int conversationId,
  ) async {
    final json = await _sendJson(
      'POST',
      '/agent/conversations/$conversationId/rewind-last-turn',
    );
    return ConversationRewindResult.fromJson(json as Map<String, dynamic>);
  }

  Future<ConversationSendAcceptedResult> sendConversationMessage({
    required int conversationId,
    required String textContent,
    required List<int> attachmentIds,
    ConversationTool? selectedTool,
    Map<String, String> context = const <String, String>{},
  }) async {
    final json = await _sendJson(
      'POST',
      '/agent/conversations/$conversationId/messages',
      body: {
        'text_content': textContent,
        'attachment_ids': attachmentIds,
        'selected_tool': selectedTool?.apiValue,
        'context': context,
      },
    );
    return ConversationSendAcceptedResult.fromJson(
      json as Map<String, dynamic>,
    );
  }

  Stream<ConversationStreamEvent> streamConversation({
    required int conversationId,
    required String streamId,
  }) async* {
    try {
      final request = http.Request(
        'GET',
        Uri.parse(
          '${_normalizedBase()}/agent/conversations/$conversationId/streams/$streamId',
        ),
      );
      request.headers.addAll(_authHeaders(includeJson: false));
      request.headers['Accept'] = 'text/event-stream';
      final response = await _httpClient.send(request);
      if (response.statusCode < 200 || response.statusCode >= 300) {
        final body = await response.stream.bytesToString();
        final decoded = _decodeJsonBody(body);
        throw ApiException(
          _extractErrorMessage(decoded, response.statusCode, body),
        );
      }

      String? currentEvent;
      final dataLines = <String>[];
      await for (final line
          in response.stream
              .transform(utf8.decoder)
              .transform(const LineSplitter())) {
        if (line.isEmpty) {
          if (currentEvent != null) {
            final dataText = dataLines.join('\n').trim();
            final decoded = dataText.isEmpty
                ? <String, dynamic>{}
                : jsonDecode(dataText) as Map<String, dynamic>;
            yield ConversationStreamEvent(event: currentEvent, data: decoded);
          }
          currentEvent = null;
          dataLines.clear();
          continue;
        }
        if (line.startsWith('event:')) {
          currentEvent = line.substring(6).trim();
        } else if (line.startsWith('data:')) {
          dataLines.add(line.substring(5).trim());
        }
      }
    } on http.ClientException catch (error) {
      throw _mapClientException(error);
    }
  }

  Future<ConversationActionResult> performConversationAction({
    required int conversationId,
    required String action,
    Map<String, dynamic> payload = const <String, dynamic>{},
  }) async {
    final json = await _sendJson(
      'POST',
      '/agent/conversations/$conversationId/actions',
      body: {'action': action, 'payload': payload},
    );
    return ConversationActionResult.fromJson(json as Map<String, dynamic>);
  }

  Future<void> deleteSchedule(int scheduleId) async {
    await _sendJson('DELETE', '/schedule/$scheduleId');
  }

  Future<ScheduleEditPreviewResult> previewScheduleEdit({
    required int scheduleId,
    required ScheduleDraft draft,
  }) async {
    final json = await _sendJson(
      'POST',
      '/schedule/$scheduleId/edits/preview',
      body: <String, dynamic>{'draft': draft.toJson()},
    );
    return ScheduleEditPreviewResult.fromJson(json as Map<String, dynamic>);
  }

  Future<ScheduleEditConfirmResult> confirmScheduleEdit({
    required int scheduleId,
    required String approvalToken,
    required ScheduleDraft normalizedDraft,
  }) async {
    final json = await _sendJson(
      'POST',
      '/schedule/$scheduleId/edits/confirm',
      body: <String, dynamic>{
        'approval_token': approvalToken,
        'normalized_draft': normalizedDraft.toJson(),
      },
    );
    return ScheduleEditConfirmResult.fromJson(json as Map<String, dynamic>);
  }

  Future<void> deleteQuickNote(int noteId) async {
    await _sendJson('DELETE', '/quick-notes/$noteId');
  }

  Future<QuickNoteItem> updateQuickNote({
    required int noteId,
    required String content,
    required List<String> tags,
  }) async {
    final json = await _sendJson(
      'PATCH',
      '/quick-notes/$noteId',
      body: <String, dynamic>{'content': content, 'tags': tags},
    );
    return QuickNoteItem.fromJson(json as Map<String, dynamic>);
  }

  Future<dynamic> _sendJson(
    String method,
    String path, {
    Map<String, dynamic>? body,
    bool authenticated = true,
  }) async {
    try {
      final uri = Uri.parse('${_normalizedBase()}$path');
      final request = http.Request(method, uri);
      request.headers.addAll(
        _authHeaders(includeJson: true, authenticated: authenticated),
      );
      request.body = body == null ? '' : jsonEncode(body);
      final response = await _httpClient.send(request);
      final responseBody = await response.stream.bytesToString();
      final decoded = _decodeJsonBody(responseBody);
      if (response.statusCode < 200 || response.statusCode >= 300) {
        throw ApiException(
          _extractErrorMessage(decoded, response.statusCode, responseBody),
        );
      }
      return decoded;
    } on http.ClientException catch (error) {
      throw _mapClientException(error);
    }
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
    return _baseUrl.endsWith('/')
        ? _baseUrl.substring(0, _baseUrl.length - 1)
        : _baseUrl;
  }

  dynamic _decodeJsonBody(String body) {
    final trimmed = body.trim();
    if (trimmed.isEmpty) {
      return null;
    }
    if (!trimmed.startsWith('{') && !trimmed.startsWith('[')) {
      return null;
    }
    try {
      return jsonDecode(trimmed);
    } on FormatException {
      return null;
    }
  }

  String _extractErrorMessage(
    dynamic decoded,
    int statusCode, [
    String rawBody = '',
  ]) {
    if (decoded is Map<String, dynamic> && decoded['detail'] is String) {
      final detail = (decoded['detail'] as String).trim();
      if (detail.isNotEmpty) {
        return detail;
      }
    }
    final trimmedBody = rawBody.trim();
    if (trimmedBody.isNotEmpty &&
        trimmedBody.toLowerCase() != 'internal server error') {
      return trimmedBody;
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

  ApiException _mapClientException(http.ClientException error) {
    final message = error.message.trim();
    final lowered = message.toLowerCase();
    if (kIsWeb &&
        (lowered.contains('failed to fetch') ||
            lowered.contains('xmlhttprequest error'))) {
      return ApiException(AppStrings.webConnectionFailed);
    }
    return ApiException(
      message.isNotEmpty ? message : AppStrings.networkRequestFailed,
    );
  }

  List<Map<String, dynamic>> _asList(dynamic json) {
    if (json is! List<dynamic>) {
      throw ApiException('返回数据格式不正确。');
    }
    return json.map((item) => item as Map<String, dynamic>).toList();
  }
}
