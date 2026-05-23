import 'package:flutter/foundation.dart';

import 'api_client.dart';
import 'models.dart';

class AppController extends ChangeNotifier {
  AppController({ApiClient? apiClient}) : _apiClient = apiClient ?? ApiClient();

  final ApiClient _apiClient;

  SessionInfo? _session;
  bool _loading = false;
  bool _conversationLoading = false;
  String? _lastError;
  List<ScheduleItem> _schedules = <ScheduleItem>[];
  List<QuickNoteItem> _quickNotes = <QuickNoteItem>[];
  List<NotificationItem> _notifications = <NotificationItem>[];
  List<ConversationThreadItem> _conversations = <ConversationThreadItem>[];
  List<ConversationMessageItem> _messages = <ConversationMessageItem>[];
  int? _activeConversationId;

  SessionInfo? get session => _session;
  bool get isAuthenticated => _session != null;
  bool get isLoading => _loading;
  bool get isConversationLoading => _conversationLoading;
  String? get lastError => _lastError;
  List<ScheduleItem> get schedules => List<ScheduleItem>.unmodifiable(_schedules);
  List<QuickNoteItem> get quickNotes => List<QuickNoteItem>.unmodifiable(_quickNotes);
  List<NotificationItem> get notifications => List<NotificationItem>.unmodifiable(_notifications);
  List<ConversationThreadItem> get conversations => List<ConversationThreadItem>.unmodifiable(_conversations);
  List<ConversationMessageItem> get messages => List<ConversationMessageItem>.unmodifiable(_messages);
  int? get activeConversationId => _activeConversationId;
  ConversationThreadItem? get activeConversation {
    final id = _activeConversationId;
    if (id == null) {
      return null;
    }
    for (final item in _conversations) {
      if (item.id == id) {
        return item;
      }
    }
    return null;
  }

  Future<void> login(String email, String password) async {
    _setLoading(true);
    try {
      _session = await _apiClient.login(email, password);
      _lastError = null;
      await loadShellData();
      await ensureConversationReady();
    } catch (error) {
      _lastError = error.toString();
      rethrow;
    } finally {
      _setLoading(false);
    }
  }

  Future<void> loadShellData() async {
    if (!isAuthenticated) {
      return;
    }
    _setLoading(true);
    try {
      final results = await Future.wait<dynamic>([
        _apiClient.fetchSchedules(),
        _apiClient.fetchQuickNotes(),
        _apiClient.fetchNotifications(),
        _apiClient.fetchConversations(),
      ]);
      _schedules = results[0] as List<ScheduleItem>;
      _quickNotes = results[1] as List<QuickNoteItem>;
      _notifications = results[2] as List<NotificationItem>;
      _conversations = results[3] as List<ConversationThreadItem>;
      if (_activeConversationId != null &&
          !_conversations.any((item) => item.id == _activeConversationId)) {
        _activeConversationId = null;
        _messages = <ConversationMessageItem>[];
      }
      _lastError = null;
    } catch (error) {
      _lastError = error.toString();
      rethrow;
    } finally {
      _setLoading(false);
    }
  }

  Future<void> ensureConversationReady() async {
    if (!isAuthenticated) {
      return;
    }
    if (_conversations.isEmpty) {
      final thread = await _apiClient.createConversation();
      _upsertConversation(thread, moveToFront: true);
    }
    final targetId = _activeConversationId ?? _conversations.first.id;
    await selectConversation(targetId);
  }

  Future<void> selectConversation(int conversationId) async {
    if (!isAuthenticated) {
      return;
    }
    _conversationLoading = true;
    notifyListeners();
    try {
      final result = await _apiClient.fetchConversationMessages(conversationId);
      final thread = result.$1;
      final items = result.$2;
      _activeConversationId = thread.id;
      _upsertConversation(thread, moveToFront: true);
      _messages = items;
      _lastError = null;
    } catch (error) {
      _lastError = error.toString();
      rethrow;
    } finally {
      _conversationLoading = false;
      notifyListeners();
    }
  }

  Future<void> createConversationAndSelect() async {
    final thread = await _apiClient.createConversation();
    _upsertConversation(thread, moveToFront: true);
    _activeConversationId = thread.id;
    _messages = <ConversationMessageItem>[];
    notifyListeners();
    await selectConversation(thread.id);
  }

  Future<void> sendChatMessage({
    required String textContent,
    required InputSourceType sourceType,
    required List<int> attachmentIds,
  }) async {
    if (!isAuthenticated) {
      return;
    }
    if (_activeConversationId == null) {
      await ensureConversationReady();
    }
    final conversationId = _activeConversationId!;
    final result = await _apiClient.sendConversationMessage(
      conversationId: conversationId,
      textContent: textContent,
      sourceType: sourceType,
      attachmentIds: attachmentIds,
    );
    _activeConversationId = result.conversation.id;
    _upsertConversation(result.conversation, moveToFront: true);
    _messages = <ConversationMessageItem>[
      ..._messages,
      result.userMessage,
      ...result.assistantMessages,
    ];
    notifyListeners();
  }

  Future<void> performConversationAction({
    required String action,
    Map<String, dynamic> payload = const <String, dynamic>{},
  }) async {
    if (!isAuthenticated || _activeConversationId == null) {
      return;
    }
    final result = await _apiClient.performConversationAction(
      conversationId: _activeConversationId!,
      action: action,
      payload: payload,
    );
    _upsertConversation(result.conversation, moveToFront: true);
    _messages = <ConversationMessageItem>[
      ..._messages,
      ...result.assistantMessages,
    ];
    await _refreshCollectionsOnly();
    notifyListeners();
  }

  Future<UploadedAttachment> uploadAttachment(
    InputSourceType sourceType,
    LocalAttachmentData attachment,
  ) {
    return _apiClient.uploadAttachment(sourceType, attachment);
  }

  Future<void> deleteScheduleItem(int scheduleId) async {
    await _apiClient.deleteSchedule(scheduleId);
    await _refreshCollectionsOnly();
    notifyListeners();
  }

  Future<void> deleteQuickNoteItem(int noteId) async {
    await _apiClient.deleteQuickNote(noteId);
    await _refreshCollectionsOnly();
    notifyListeners();
  }

  Future<void> refreshNotifications() async {
    _notifications = await _apiClient.fetchNotifications();
    notifyListeners();
  }

  void logout() {
    _session = null;
    _apiClient.setAccessToken(null);
    _schedules = <ScheduleItem>[];
    _quickNotes = <QuickNoteItem>[];
    _notifications = <NotificationItem>[];
    _conversations = <ConversationThreadItem>[];
    _messages = <ConversationMessageItem>[];
    _activeConversationId = null;
    _lastError = null;
    notifyListeners();
  }

  Future<void> _refreshCollectionsOnly() async {
    if (!isAuthenticated) {
      return;
    }
    final results = await Future.wait<dynamic>([
      _apiClient.fetchSchedules(),
      _apiClient.fetchQuickNotes(),
      _apiClient.fetchNotifications(),
      _apiClient.fetchConversations(),
    ]);
    _schedules = results[0] as List<ScheduleItem>;
    _quickNotes = results[1] as List<QuickNoteItem>;
    _notifications = results[2] as List<NotificationItem>;
    _conversations = results[3] as List<ConversationThreadItem>;
  }

  void _upsertConversation(ConversationThreadItem item, {required bool moveToFront}) {
    final updated = <ConversationThreadItem>[];
    if (moveToFront) {
      updated.add(item);
    }
    for (final current in _conversations) {
      if (current.id != item.id) {
        updated.add(current);
      }
    }
    if (!moveToFront && !_conversations.any((current) => current.id == item.id)) {
      updated.add(item);
    }
    _conversations = updated;
  }

  void _setLoading(bool value) {
    _loading = value;
    notifyListeners();
  }
}
