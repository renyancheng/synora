import 'dart:async';

import 'package:flutter/foundation.dart';

import 'api_client.dart';
import 'local_session_store.dart';
import 'models.dart';
import 'strings.dart';

const int draftConversationId = 0;
const Set<String> _nonEditableCardMessageTypes = <String>{
  'schedule_draft_card',
  'quick_note_preview_card',
  'conflict_card',
};

class ConversationViewState {
  ConversationViewState({
    required this.messages,
    this.isLoading = false,
    this.isSending = false,
    this.streamStatusLabel,
    this.lastError,
    this.draftText = '',
    this.draftAttachments = const <ComposerAttachment>[],
    this.draftTool,
    this.isDraft = false,
  });

  final List<ConversationMessageItem> messages;
  final bool isLoading;
  final bool isSending;
  final String? streamStatusLabel;
  final String? lastError;
  final String draftText;
  final List<ComposerAttachment> draftAttachments;
  final ConversationTool? draftTool;
  final bool isDraft;

  ConversationViewState copyWith({
    List<ConversationMessageItem>? messages,
    bool? isLoading,
    bool? isSending,
    String? streamStatusLabel,
    bool clearStreamStatusLabel = false,
    String? lastError,
    bool clearLastError = false,
    String? draftText,
    List<ComposerAttachment>? draftAttachments,
    ConversationTool? draftTool,
    bool clearDraftTool = false,
    bool? isDraft,
  }) {
    return ConversationViewState(
      messages: messages ?? this.messages,
      isLoading: isLoading ?? this.isLoading,
      isSending: isSending ?? this.isSending,
      streamStatusLabel: clearStreamStatusLabel
          ? null
          : (streamStatusLabel ?? this.streamStatusLabel),
      lastError: clearLastError ? null : (lastError ?? this.lastError),
      draftText: draftText ?? this.draftText,
      draftAttachments: draftAttachments ?? this.draftAttachments,
      draftTool: clearDraftTool ? null : (draftTool ?? this.draftTool),
      isDraft: isDraft ?? this.isDraft,
    );
  }

  static ConversationViewState draft() => ConversationViewState(
    messages: const <ConversationMessageItem>[],
    isDraft: true,
  );
}

class AppController extends ChangeNotifier {
  AppController({ApiClient? apiClient, LocalSessionStore? sessionStore})
    : _apiClient = apiClient ?? ApiClient(),
      _sessionStore = sessionStore ?? LocalSessionStore() {
    unawaited(_restorePersistedSession());
  }

  final ApiClient _apiClient;
  final LocalSessionStore _sessionStore;

  SessionInfo? _session;
  UserProfile? _lastKnownUser;
  bool _loading = false;
  bool _restoringSession = true;
  int _nextTempMessageId = -1;
  List<ScheduleItem> _schedules = <ScheduleItem>[];
  List<QuickNoteItem> _quickNotes = <QuickNoteItem>[];
  List<NotificationItem> _notifications = <NotificationItem>[];
  String _memorySummary = '';
  List<MemoryItem> _memoryItems = <MemoryItem>[];
  UserPreferences _userPreferences = UserPreferences(wecomRobotWebhook: null);
  List<ConversationThreadItem> _conversations = <ConversationThreadItem>[];
  final Map<int, ConversationViewState> _conversationStates =
      <int, ConversationViewState>{
        draftConversationId: ConversationViewState.draft(),
      };
  int _activeConversationId = draftConversationId;

  SessionInfo? get session => _session;
  UserProfile? get lastKnownUser => _lastKnownUser;
  bool get isAuthenticated => _session != null;
  bool get isLoading => _loading;
  bool get isRestoringSession => _restoringSession;
  List<ScheduleItem> get schedules =>
      List<ScheduleItem>.unmodifiable(_schedules);
  List<QuickNoteItem> get quickNotes =>
      List<QuickNoteItem>.unmodifiable(_quickNotes);
  List<NotificationItem> get notifications =>
      List<NotificationItem>.unmodifiable(_notifications);
  String get memorySummary => _memorySummary;
  List<MemoryItem> get memoryItems =>
      List<MemoryItem>.unmodifiable(_memoryItems);
  UserPreferences get userPreferences => _userPreferences;
  List<ConversationThreadItem> get conversations =>
      List<ConversationThreadItem>.unmodifiable(_conversations);
  int get activeConversationId => _activeConversationId;
  bool get isDraftConversation => _activeConversationId == draftConversationId;

  ConversationViewState get activeState =>
      _conversationStates[_activeConversationId] ??
      ConversationViewState.draft();
  List<ConversationMessageItem> get messages =>
      List<ConversationMessageItem>.unmodifiable(activeState.messages);
  bool get isConversationLoading => activeState.isLoading;
  bool get isMessageSending => activeState.isSending;
  String? get lastError => activeState.lastError;
  String? get streamStatusLabel => activeState.streamStatusLabel;
  String get draftText => activeState.draftText;
  List<ComposerAttachment> get draftAttachments =>
      List<ComposerAttachment>.unmodifiable(activeState.draftAttachments);
  ConversationTool? get draftTool => activeState.draftTool;

  int? get latestUserMessageId {
    for (final message in activeState.messages.reversed) {
      if (message.isUser) {
        return message.id;
      }
    }
    return null;
  }

  ConversationThreadItem? get activeConversation {
    if (isDraftConversation) {
      return null;
    }
    for (final item in _conversations) {
      if (item.id == _activeConversationId) {
        return item;
      }
    }
    return null;
  }

  Future<void> _restorePersistedSession() async {
    _restoringSession = true;
    try {
      _lastKnownUser = await _sessionStore.readLastKnownUser();
      final persisted = await _sessionStore.readSession();
      if (persisted == null) {
        return;
      }
      _apiClient.setAccessToken(persisted.accessToken);
      final restored = await _apiClient.fetchCurrentSession();
      _session = SessionInfo(
        accessToken: persisted.accessToken,
        expiresAt: restored.expiresAt,
        user: restored.user,
      );
      _lastKnownUser = restored.user;
      await _sessionStore.saveSession(_session!);
      await loadShellData();
      beginDraftConversation(notify: false);
    } catch (_) {
      _session = null;
      _apiClient.setAccessToken(null);
      await _sessionStore.clearSessionToken();
    } finally {
      _restoringSession = false;
      notifyListeners();
    }
  }

  Future<void> _persistSession(SessionInfo session) async {
    _lastKnownUser = session.user;
    await _sessionStore.saveSession(session);
  }

  Future<void> login(String email, String password) async {
    _setLoading(true);
    try {
      _session = await _apiClient.login(email, password);
      await _persistSession(_session!);
      await loadShellData();
      beginDraftConversation(notify: false);
    } finally {
      _setLoading(false);
      notifyListeners();
    }
  }

  Future<void> register({
    required String email,
    required String password,
    required String displayName,
  }) async {
    _setLoading(true);
    try {
      _session = await _apiClient.register(
        email: email,
        password: password,
        displayName: displayName,
      );
      await _persistSession(_session!);
      await loadShellData();
      beginDraftConversation(notify: false);
    } finally {
      _setLoading(false);
      notifyListeners();
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
        _apiClient.fetchUserPreferences(),
      ]);
      _schedules = results[0] as List<ScheduleItem>;
      _quickNotes = results[1] as List<QuickNoteItem>;
      _notifications = results[2] as List<NotificationItem>;
      _conversations = (results[3] as List<ConversationThreadItem>)
        ..sort((a, b) => b.createdAt.compareTo(a.createdAt));
      _userPreferences = results[4] as UserPreferences;
      if (!isDraftConversation &&
          !_conversations.any((item) => item.id == _activeConversationId)) {
        beginDraftConversation(notify: false);
      }
    } finally {
      _setLoading(false);
    }
  }

  void beginDraftConversation({bool notify = true}) {
    if (isDraftConversation) {
      if (notify) {
        notifyListeners();
      }
      return;
    }
    _conversationStates[draftConversationId] = ConversationViewState.draft();
    _activeConversationId = draftConversationId;
    if (notify) {
      notifyListeners();
    }
  }

  Future<void> ensureConversationReady() async {
    if (!isAuthenticated) {
      return;
    }
    if (!_conversationStates.containsKey(draftConversationId)) {
      _conversationStates[draftConversationId] = ConversationViewState.draft();
    }
    _activeConversationId = draftConversationId;
    notifyListeners();
  }

  Future<void> selectConversation(int conversationId) async {
    if (!isAuthenticated) {
      return;
    }
    if (conversationId == draftConversationId) {
      beginDraftConversation();
      return;
    }
    if (isDraftConversation) {
      _conversationStates[draftConversationId] = ConversationViewState.draft();
    }
    _activeConversationId = conversationId;
    final current =
        _conversationStates[conversationId] ??
        ConversationViewState(messages: const <ConversationMessageItem>[]);
    _conversationStates[conversationId] = current.copyWith(
      isLoading: true,
      clearLastError: true,
      clearStreamStatusLabel: true,
      isDraft: false,
    );
    notifyListeners();
    try {
      final result = await _apiClient.fetchConversationMessages(conversationId);
      final thread = result.$1;
      final items = result.$2;
      _upsertConversation(thread);
      _conversationStates[conversationId] = current.copyWith(
        messages: items,
        isLoading: false,
        isDraft: false,
        clearLastError: true,
        clearStreamStatusLabel: true,
      );
    } catch (error) {
      _conversationStates[conversationId] = current.copyWith(
        isLoading: false,
        lastError: error.toString(),
      );
      rethrow;
    } finally {
      notifyListeners();
    }
  }

  Future<void> createConversationAndSelect() async {
    if (isDraftConversation) {
      return;
    }
    beginDraftConversation();
  }

  void updateDraftText(String value) {
    _setStateFor(_activeConversationId, activeState.copyWith(draftText: value));
  }

  void setDraftTool(ConversationTool? tool) {
    _setStateFor(
      _activeConversationId,
      activeState.copyWith(draftTool: tool, clearDraftTool: tool == null),
    );
  }

  void addDraftAttachments(List<ComposerAttachment> attachments) {
    final next = List<ComposerAttachment>.from(activeState.draftAttachments)
      ..addAll(attachments);
    _setStateFor(
      _activeConversationId,
      activeState.copyWith(draftAttachments: next),
    );
  }

  void removeDraftAttachmentAt(int index) {
    final next = List<ComposerAttachment>.from(activeState.draftAttachments);
    if (index < 0 || index >= next.length) {
      return;
    }
    next.removeAt(index);
    _setStateFor(
      _activeConversationId,
      activeState.copyWith(draftAttachments: next),
    );
  }

  void refillComposerFromMessage(ConversationMessageItem message) {
    final attachments = message.localAttachments.isNotEmpty
        ? message.localAttachments.map(ComposerAttachment.local).toList()
        : message.attachmentRefs.map(ComposerAttachment.remote).toList();
    _setStateFor(
      _activeConversationId,
      activeState.copyWith(
        draftText: message.textContent ?? '',
        draftAttachments: attachments,
        draftTool: message.selectedTool,
      ),
    );
  }

  bool canEditMessage(ConversationMessageItem message) {
    if (!message.isUser) {
      return false;
    }
    if (latestUserMessageId != message.id) {
      return false;
    }
    if (message.status == 'failed') {
      return true;
    }
    final messages = activeState.messages;
    final index = messages.indexWhere((item) => item.id == message.id);
    if (index < 0) {
      return false;
    }
    for (final item in messages.skip(index + 1)) {
      if (_nonEditableCardMessageTypes.contains(item.messageType)) {
        return false;
      }
    }
    return true;
  }

  Future<void> sendChatMessage() async {
    if (!isAuthenticated) {
      return;
    }
    final state = activeState;
    final textContent = state.draftText.trim();
    final attachments = List<ComposerAttachment>.from(state.draftAttachments);
    final selectedTool = state.draftTool;
    if (textContent.isEmpty && attachments.isEmpty) {
      throw ApiException(AppStrings.sendEmptyMessage);
    }

    var conversationId = _activeConversationId;
    ConversationThreadItem? thread;
    if (conversationId == draftConversationId) {
      thread = await _apiClient.createConversation();
      conversationId = thread.id;
      _upsertConversation(thread);
      _conversationStates[conversationId] = ConversationViewState(
        messages: const <ConversationMessageItem>[],
        isDraft: false,
      );
      _activeConversationId = conversationId;
      _conversationStates.remove(draftConversationId);
    }

    final tempUserId = _nextTempMessageId--;
    final tempPayload = <String, dynamic>{
      if (selectedTool != null) 'selected_tool': selectedTool.apiValue,
      if (attachments.any((item) => !item.isLocal))
        'attachment_refs': attachments
            .where((item) => !item.isLocal)
            .map(
              (item) =>
                  item.remote?.toJson() ??
                  <String, dynamic>{
                    'attachment_id': -1,
                    'file_name': item.fileName,
                    'content_type': '',
                  },
            )
            .toList(),
    };
    final tempUserMessage = ConversationMessageItem.local(
      id: tempUserId,
      role: 'user',
      messageType: 'text',
      status: 'sending',
      textContent: textContent,
      structuredPayload: tempPayload,
      localAttachments: attachments
          .where((item) => item.isLocal)
          .map((item) => item.local!)
          .toList(),
    );
    final existingMessages = List<ConversationMessageItem>.from(
      (_conversationStates[conversationId] ??
              ConversationViewState(
                messages: const <ConversationMessageItem>[],
              ))
          .messages,
    )..add(tempUserMessage);
    _conversationStates[conversationId] =
        (_conversationStates[conversationId] ??
                ConversationViewState(
                  messages: const <ConversationMessageItem>[],
                ))
            .copyWith(
              messages: existingMessages,
              isSending: true,
              clearLastError: true,
              clearStreamStatusLabel: true,
              draftText: '',
              draftAttachments: const <ComposerAttachment>[],
              clearDraftTool: true,
              isDraft: false,
            );
    notifyListeners();

    int? assistantMessageId;
    try {
      final uploadedIds = <int>[];
      for (final attachment in attachments) {
        if (attachment.isLocal) {
          final uploaded = await _apiClient.uploadAttachment(attachment.local!);
          uploadedIds.add(uploaded.attachmentId);
        } else {
          uploadedIds.add(attachment.remote!.attachmentId);
        }
      }

      final accepted = await _apiClient.sendConversationMessage(
        conversationId: conversationId,
        textContent: textContent,
        attachmentIds: uploadedIds,
        selectedTool: selectedTool,
      );
      _replaceMessage(
        conversationId,
        tempUserId,
        accepted.userMessage.copyWith(status: 'sent'),
      );
      assistantMessageId = accepted.assistantMessageId;
      _replaceOrAppendMessage(
        conversationId,
        ConversationMessageItem.local(
          id: assistantMessageId,
          role: 'assistant',
          messageType: 'text',
          status: 'streaming',
          textContent: '',
        ),
      );
      notifyListeners();

      await for (final event in _apiClient.streamConversation(
        conversationId: conversationId,
        streamId: accepted.streamId,
      )) {
        _handleStreamEvent(
          conversationId,
          event,
          assistantMessageId: assistantMessageId,
        );
      }
      await _refreshCollectionsOnly();
    } catch (error) {
      final failureText = AppStrings.chatFailureReason(null, error.toString());
      _markMessageFailed(conversationId, tempUserId);
      if (assistantMessageId != null) {
        _replaceOrAppendMessage(
          conversationId,
          ConversationMessageItem.local(
            id: assistantMessageId,
            role: 'assistant',
            messageType: 'text',
            status: 'failed',
            textContent: failureText,
          ),
        );
      }
      _setStateFor(
        conversationId,
        (_conversationStates[conversationId] ??
                ConversationViewState(
                  messages: const <ConversationMessageItem>[],
                ))
            .copyWith(
              lastError: failureText,
              isSending: false,
              clearStreamStatusLabel: true,
            ),
      );
      notifyListeners();
      rethrow;
    } finally {
      final current = _conversationStates[conversationId];
      if (current != null) {
        _conversationStates[conversationId] = current.copyWith(
          isSending: false,
          clearStreamStatusLabel: true,
        );
      }
      notifyListeners();
    }
  }

  Future<void> performConversationAction({
    required String action,
    Map<String, dynamic> payload = const <String, dynamic>{},
  }) async {
    if (!isAuthenticated || isDraftConversation) {
      return;
    }
    final conversationId = _activeConversationId;
    final result = await _apiClient.performConversationAction(
      conversationId: conversationId,
      action: action,
      payload: payload,
    );
    _upsertConversation(result.conversation);
    final refreshed = await _apiClient.fetchConversationMessages(
      conversationId,
    );
    _conversationStates[conversationId] =
        (_conversationStates[conversationId] ??
                ConversationViewState(
                  messages: const <ConversationMessageItem>[],
                ))
            .copyWith(
              messages: refreshed.$2,
              isLoading: false,
              isDraft: false,
              clearLastError: true,
            );
    await _refreshCollectionsOnly();
    notifyListeners();
  }

  Future<void> renameConversation({
    required int conversationId,
    required String title,
  }) async {
    final updated = await _apiClient.renameConversation(
      conversationId: conversationId,
      title: title,
    );
    _upsertConversation(updated);
    notifyListeners();
  }

  Future<void> deleteConversation(int conversationId) async {
    await _apiClient.deleteConversation(conversationId);
    _conversations =
        _conversations.where((item) => item.id != conversationId).toList()
          ..sort((a, b) => b.createdAt.compareTo(a.createdAt));
    _conversationStates.remove(conversationId);
    if (_activeConversationId == conversationId) {
      beginDraftConversation(notify: false);
    }
    notifyListeners();
  }

  Future<void> editResendMessage(ConversationMessageItem message) async {
    if (!canEditMessage(message)) {
      return;
    }
    if (message.status == 'failed') {
      final messages = List<ConversationMessageItem>.from(activeState.messages)
        ..removeWhere((item) => item.id == message.id);
      final attachments = message.localAttachments.isNotEmpty
          ? message.localAttachments.map(ComposerAttachment.local).toList()
          : message.attachmentRefs.map(ComposerAttachment.remote).toList();
      _setStateFor(
        _activeConversationId,
        activeState.copyWith(
          messages: messages,
          draftText: message.textContent ?? '',
          draftAttachments: attachments,
          draftTool: message.selectedTool,
          clearLastError: true,
        ),
      );
      return;
    }
    if (isDraftConversation) {
      refillComposerFromMessage(message);
      return;
    }
    final result = await _apiClient.rewindConversationLastTurn(
      _activeConversationId,
    );
    final refreshed = await _apiClient.fetchConversationMessages(
      _activeConversationId,
    );
    final restored = result.restoredMessage;
    final attachments = restored.attachmentRefs
        .map(ComposerAttachment.remote)
        .toList();
    _upsertConversation(result.conversation);
    _conversationStates[_activeConversationId] =
        (_conversationStates[_activeConversationId] ??
                ConversationViewState(
                  messages: const <ConversationMessageItem>[],
                ))
            .copyWith(
              messages: refreshed.$2,
              draftText: restored.textContent ?? '',
              draftAttachments: attachments,
              draftTool: restored.selectedTool,
              isDraft: false,
              clearLastError: true,
              clearStreamStatusLabel: true,
            );
    await _refreshCollectionsOnly();
    notifyListeners();
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

  Future<ScheduleEditPreviewResult> previewScheduleEdit({
    required int scheduleId,
    required ScheduleDraft draft,
  }) {
    return _apiClient.previewScheduleEdit(scheduleId: scheduleId, draft: draft);
  }

  Future<ScheduleItem> confirmScheduleEdit({
    required int scheduleId,
    required String approvalToken,
    required ScheduleDraft draft,
  }) async {
    final result = await _apiClient.confirmScheduleEdit(
      scheduleId: scheduleId,
      approvalToken: approvalToken,
      normalizedDraft: draft,
    );
    await _refreshCollectionsOnly();
    notifyListeners();
    return result.schedule;
  }

  Future<QuickNoteItem> updateQuickNote({
    required int noteId,
    required String content,
    required List<String> tags,
  }) async {
    final item = await _apiClient.updateQuickNote(
      noteId: noteId,
      content: content,
      tags: tags,
    );
    await _refreshCollectionsOnly();
    notifyListeners();
    return item;
  }

  Future<void> refreshNotifications() async {
    _notifications = await _apiClient.fetchNotifications();
    notifyListeners();
  }

  Future<void> refreshMemory() async {
    final result = await _apiClient.fetchMemory();
    _memorySummary = result.summary;
    _memoryItems = result.items;
    notifyListeners();
  }

  Future<void> deleteMemoryItem(int memoryId) async {
    await _apiClient.deleteMemory(memoryId);
    await refreshMemory();
  }

  Future<void> clearAllMemory() async {
    await _apiClient.clearMemory();
    await refreshMemory();
  }

  Future<List<QuickNoteTagItem>> fetchQuickNoteTags() {
    return _apiClient.fetchQuickNoteTags();
  }

  Future<List<QuickNoteItem>> fetchQuickNotesByTag(
    String? tag, {
    String? query,
  }) {
    return _apiClient.fetchQuickNotes(tag: tag, query: query);
  }

  Future<List<ScheduleItem>> fetchSchedules({String? query}) {
    return _apiClient.fetchSchedules(query: query);
  }

  Future<void> updateUserPreferences({
    required String? wecomRobotWebhook,
  }) async {
    _userPreferences = await _apiClient.updateUserPreferences(
      wecomRobotWebhook: wecomRobotWebhook,
    );
    notifyListeners();
  }

  Future<void> logout() async {
    _lastKnownUser = _session?.user ?? _lastKnownUser;
    try {
      await _apiClient.logout();
    } catch (_) {
      _apiClient.setAccessToken(null);
    }
    await _sessionStore.clearSessionToken();
    _session = null;
    _schedules = <ScheduleItem>[];
    _quickNotes = <QuickNoteItem>[];
    _notifications = <NotificationItem>[];
    _memorySummary = '';
    _memoryItems = <MemoryItem>[];
    _conversations = <ConversationThreadItem>[];
    _conversationStates
      ..clear()
      ..[draftConversationId] = ConversationViewState.draft();
    _activeConversationId = draftConversationId;
    _userPreferences = UserPreferences(wecomRobotWebhook: null);
    notifyListeners();
  }

  void _handleStreamEvent(
    int conversationId,
    ConversationStreamEvent event, {
    required int assistantMessageId,
  }) {
    switch (event.event) {
      case 'run_started':
        _replaceOrAppendMessage(
          conversationId,
          ConversationMessageItem.local(
            id: assistantMessageId,
            role: 'assistant',
            messageType: 'text',
            status: 'streaming',
            textContent: '',
          ),
        );
        break;
      case 'message_delta':
        final delta = event.data['delta'] as String? ?? '';
        final current = _conversationStates[conversationId];
        if (current == null) {
          break;
        }
        final messages = List<ConversationMessageItem>.from(current.messages);
        final index = messages.indexWhere(
          (item) => item.id == assistantMessageId,
        );
        if (index >= 0) {
          final message = messages[index];
          messages[index] = message.copyWith(
            status: 'streaming',
            textContent: '${message.textContent ?? ''}$delta',
          );
          _conversationStates[conversationId] = current.copyWith(
            messages: messages,
          );
        }
        break;
      case 'message_completed':
        final messageJson =
            event.data['message'] as Map<String, dynamic>? ??
            <String, dynamic>{};
        _replaceOrAppendMessage(
          conversationId,
          ConversationMessageItem.fromJson(messageJson),
        );
        _setStateFor(
          conversationId,
          _conversationStates[conversationId]!.copyWith(
            clearStreamStatusLabel: true,
          ),
        );
        break;
      case 'card_snapshot':
        final messageJson =
            event.data['message'] as Map<String, dynamic>? ??
            <String, dynamic>{};
        _replaceOrAppendMessage(
          conversationId,
          ConversationMessageItem.fromJson(messageJson),
        );
        break;
      case 'tool_call_started':
        _setStateFor(
          conversationId,
          _conversationStates[conversationId]!.copyWith(
            streamStatusLabel: _toolStatusLabel(
              event.data['tool_name'] as String?,
            ),
          ),
        );
        break;
      case 'tool_call_completed':
      case 'tool_call_failed':
        _setStateFor(
          conversationId,
          _conversationStates[conversationId]!.copyWith(
            clearStreamStatusLabel: true,
          ),
        );
        break;
      case 'run_failed':
        final failureText = AppStrings.chatFailureReason(
          event.data['code'] as String?,
          event.data['message'] as String?,
        );
        final current = _conversationStates[conversationId];
        if (current != null) {
          final messages = List<ConversationMessageItem>.from(current.messages);
          final index = messages.indexWhere(
            (item) => item.id == assistantMessageId,
          );
          if (index >= 0) {
            final message = messages[index];
            messages[index] = message.copyWith(
              status: 'failed',
              textContent: (message.textContent ?? '').trim().isNotEmpty
                  ? message.textContent
                  : failureText,
            );
          }
          _conversationStates[conversationId] = current.copyWith(
            messages: messages,
            lastError: failureText,
            isSending: false,
            clearStreamStatusLabel: true,
          );
        }
        break;
      case 'run_completed':
        final current = _conversationStates[conversationId];
        if (current != null) {
          final messages = List<ConversationMessageItem>.from(current.messages);
          final index = messages.indexWhere(
            (item) => item.id == assistantMessageId,
          );
          if (index >= 0) {
            messages[index] = messages[index].copyWith(status: 'completed');
          }
          _conversationStates[conversationId] = current.copyWith(
            messages: messages,
            clearStreamStatusLabel: true,
            isSending: false,
          );
        }
        break;
    }
    notifyListeners();
  }

  String _toolStatusLabel(String? toolName) {
    switch (toolName) {
      case 'parse_schedule_draft':
        return '正在整理日程草稿';
      case 'detect_schedule_conflicts':
        return '正在检查时间冲突';
      case 'prepare_quick_note_draft':
        return '正在整理速记内容';
      case 'create_schedule_after_approval':
        return '正在创建日程并安排提醒';
      case 'create_quick_note_after_approval':
        return '正在保存速记';
      default:
        return '正在处理';
    }
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
      _apiClient.fetchUserPreferences(),
    ]);
    _schedules = results[0] as List<ScheduleItem>;
    _quickNotes = results[1] as List<QuickNoteItem>;
    _notifications = results[2] as List<NotificationItem>;
    _conversations = (results[3] as List<ConversationThreadItem>)
      ..sort((a, b) => b.createdAt.compareTo(a.createdAt));
    _userPreferences = results[4] as UserPreferences;
  }

  void _replaceMessage(
    int conversationId,
    int targetId,
    ConversationMessageItem replacement,
  ) {
    final current =
        _conversationStates[conversationId] ??
        ConversationViewState(messages: const <ConversationMessageItem>[]);
    final messages = List<ConversationMessageItem>.from(current.messages);
    final index = messages.indexWhere((item) => item.id == targetId);
    if (index >= 0) {
      messages[index] = replacement;
    } else {
      messages.add(replacement);
    }
    _conversationStates[conversationId] = current.copyWith(messages: messages);
  }

  void _markMessageFailed(int conversationId, int targetId) {
    final current = _conversationStates[conversationId];
    if (current == null) {
      return;
    }
    final messages = List<ConversationMessageItem>.from(current.messages);
    final index = messages.indexWhere((item) => item.id == targetId);
    if (index >= 0) {
      messages[index] = messages[index].copyWith(status: 'failed');
      _conversationStates[conversationId] = current.copyWith(
        messages: messages,
      );
    }
  }

  void _replaceOrAppendMessage(
    int conversationId,
    ConversationMessageItem item,
  ) {
    final current =
        _conversationStates[conversationId] ??
        ConversationViewState(messages: const <ConversationMessageItem>[]);
    final messages = List<ConversationMessageItem>.from(current.messages);
    final index = messages.indexWhere((message) => message.id == item.id);
    if (index >= 0) {
      messages[index] = item;
    } else {
      messages.add(item);
    }
    _conversationStates[conversationId] = current.copyWith(messages: messages);
  }

  void _upsertConversation(ConversationThreadItem item) {
    final filtered = _conversations
        .where((current) => current.id != item.id)
        .toList();
    filtered.add(item);
    filtered.sort((a, b) => b.createdAt.compareTo(a.createdAt));
    _conversations = filtered;
  }

  void _setStateFor(int conversationId, ConversationViewState state) {
    _conversationStates[conversationId] = state;
    notifyListeners();
  }

  void _setLoading(bool value) {
    _loading = value;
    notifyListeners();
  }
}
