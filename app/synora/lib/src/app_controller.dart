import 'dart:async';

import 'package:flutter/foundation.dart';

import 'api_client.dart';
import 'local_session_store.dart';
import 'models.dart';
import 'strings.dart';
import 'system_notification_service.dart';

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
  UserPreferences _userPreferences = UserPreferences();
  List<ConversationThreadItem> _conversations = <ConversationThreadItem>[];
  // 实时推理轨迹：conversationId -> 进行中的步骤，card_snapshot 落库后清空
  final Map<int, List<ReasoningStepItem>> _liveReasoningSteps =
      <int, List<ReasoningStepItem>>{};
  // 当前激活的 SSE 流（供 stop 中断）：conversationId + streamId + subscription
  StreamSubscription<ConversationStreamEvent>? _activeStreamSubscription;
  int? _activeStreamConversationId;
  String? _activeStreamId;
  // 已请求中断的会话：中断后不再让 run_completed 等收尾事件清掉"生成中断"状态。
  final Set<int> _interruptedConversationIds = <int>{};
  // 最新一次生成留下的规划/感知文本（按会话隔离）：生成完成后静态常驻在气泡上方。
  final Map<int, String> _finalPlanText = <int, String>{};
  // 无障碍语义播报：仅在状态切换时更新（发送/生成/工具/完成/取消/失败），
  // 不随 message_delta 逐 token 更新，避免读屏器逐字播报。
  String? _streamAnnouncement;
  // 系统通知轮询：每 20s 拉取一次 system 审计，新送达的弹本地通知。
  Timer? _notificationPollTimer;
  final Set<int> _seenNotificationIds = <int>{};
  // FCM 设备令牌：登录后上报 /devices/register，logout 注销。
  String? _fcmToken;
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
  List<ReasoningStepItem> liveReasoningStepsFor(int conversationId) =>
      _liveReasoningSteps[conversationId] ?? const <ReasoningStepItem>[];
  /// 最新一次生成留下的规划/感知文本（生成完成后静态常驻），按会话隔离。
  String? finalPlanTextFor(int conversationId) => _finalPlanText[conversationId];

  bool get isConversationLoading => activeState.isLoading;
  bool get isMessageSending => activeState.isSending;
  String? get lastError => activeState.lastError;
  String? get streamStatusLabel => activeState.streamStatusLabel;
  /// 无障碍语义播报文案（状态切换时更新，供 liveRegion 语义节点使用）。
  String? get streamAnnouncement => _streamAnnouncement;
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
      _liveReasoningSteps.clear();
      await loadShellData();
      await _registerPendingFcmToken();
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
      _liveReasoningSteps.clear();
      await loadShellData();
      await _registerPendingFcmToken();
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
      await _registerPendingFcmToken();
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
      _seedSeenNotificationIds(_notifications);
      _startNotificationPolling();
      if (!isDraftConversation &&
          !_conversations.any((item) => item.id == _activeConversationId)) {
        beginDraftConversation(notify: false);
      }
    } finally {
      _setLoading(false);
    }
  }

  void _startNotificationPolling() {
    _notificationPollTimer?.cancel();
    _notificationPollTimer = Timer.periodic(
      const Duration(seconds: 20),
      (_) => _pollNotifications(),
    );
  }

  void _stopNotificationPolling() {
    _notificationPollTimer?.cancel();
    _notificationPollTimer = null;
  }

  void _seedSeenNotificationIds(List<NotificationItem> items) {
    _seenNotificationIds.clear();
    for (final item in items) {
      if (item.channel == 'system' && item.status == 'delivered') {
        _seenNotificationIds.add(item.id);
      }
    }
  }

  /// 拉取 system 审计：新送达的通知弹本地系统通知，避免重复弹出。
  Future<void> _pollNotifications() async {
    if (!isAuthenticated) {
      return;
    }
    try {
      final items = await _apiClient.fetchNotifications();
      for (final item in items) {
        if (item.channel == 'system' &&
            item.status == 'delivered' &&
            _seenNotificationIds.add(item.id)) {
          await SystemNotificationService.instance.show(
            id: item.id,
            title: item.subject,
            body: item.body ?? item.subject,
          );
        }
      }
      if (items.length != _notifications.length) {
        _notifications = items;
        notifyListeners();
      }
    } catch (_) {
      // 轮询失败静默，下轮重试。
    }
  }

  @override
  void dispose() {
    _notificationPollTimer?.cancel();
    super.dispose();
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

  /// 失败回答可重试、已完成回答可重新生成。
  /// 只允许当前会话最后一轮的最后一条 assistant 文本消息：绑定会话与消息，
  /// 避免误操作其他会话或历史轮次；生成中/取消中不提供操作。
  bool canRetryOrRegenerate(ConversationMessageItem message) {
    if (!message.isAssistant || message.messageType != 'text') {
      return false;
    }
    if (isDraftConversation) {
      return false;
    }
    final messages = activeState.messages;
    if (messages.isEmpty || messages.last.id != message.id) {
      return false;
    }
    return message.status == 'failed' || message.status == 'completed';
  }

  /// 重试失败回答 / 重新生成已完成回答：撤回最后一轮（后端删除该 assistant
  /// 与对应用户消息并恢复原文），恢复原文到输入框后立即自动重发。
  Future<void> retryOrRegenerateMessage(ConversationMessageItem message) async {
    if (!canRetryOrRegenerate(message)) {
      return;
    }
    final conversationId = _activeConversationId;
    final result = await _apiClient.rewindConversationLastTurn(conversationId);
    final restored = result.restoredMessage;
    final attachments = restored.attachmentRefs
        .map(ComposerAttachment.remote)
        .toList();
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
              draftText: restored.textContent ?? '',
              draftAttachments: attachments,
              draftTool: restored.selectedTool,
              isDraft: false,
              clearLastError: true,
              clearStreamStatusLabel: true,
            );
    _finalPlanText.remove(conversationId);
    await _refreshCollectionsOnly();
    notifyListeners();
    await sendChatMessage();
  }

  Future<void> sendChatMessage() async {
    if (!isAuthenticated) {
      return;
    }
    if (isMessageSending) {
      // 幂等守卫：Ctrl+Enter / 重复点击时忽略，防止重复发送
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
    // 新一轮生成开始：解除该会话之前的"已中断"标记并让旧规划行让位。
    _interruptedConversationIds.remove(conversationId);
    _finalPlanText.remove(conversationId);

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
    _streamAnnouncement = AppStrings.streamAnnounceSending;
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

      // 订阅 SSE：改为监听式以便 stop 中断（subscription 可 cancel + 后端 abort）
      final streamId = accepted.streamId;
      final assistantId = assistantMessageId;
      _activeStreamConversationId = conversationId;
      _activeStreamId = streamId;
      _activeStreamSubscription = _apiClient
          .streamConversation(
            conversationId: conversationId,
            streamId: streamId,
          )
          .listen(
            (event) => _handleStreamEvent(
              conversationId,
              event,
              assistantMessageId: assistantId,
            ),
            onError: (Object error) {
              _finishStreamFailed(
                conversationId,
                tempUserId,
                assistantMessageId,
                error,
              );
            },
            onDone: () {
              _clearActiveStream();
              unawaited(_refreshCollectionsOnly());
            },
          );
    } catch (error) {
      _finishStreamFailed(
        conversationId,
        tempUserId,
        assistantMessageId,
        error,
      );
      rethrow;
    }
  }

  /// 停止当前正在生成的消息：通知后端 abort + 取消本地订阅 + 复位状态。
  Future<void> stopCurrentGeneration() async {
    final conversationId = _activeStreamConversationId;
    final streamId = _activeStreamId;
    final subscription = _activeStreamSubscription;
    if (conversationId == null) {
      return;
    }
    if (streamId != null) {
      try {
        await _apiClient.abortConversationStream(
          conversationId: conversationId,
          streamId: streamId,
        );
      } catch (_) {
        // abort 失败不阻塞本地中断；后端检查点仍会自行收口。
      }
    }
    // 取消订阅是清理操作，fire-and-forget 即可：立即复位本地状态让停止更即时，
    // 订阅取消完成时 onDone 的幂等收口（_clearActiveStream + 刷新）无害。
    unawaited(subscription?.cancel());
    _clearActiveStream();
    // 标记该会话已中断：后续 run_completed 等收尾事件不得清掉"生成中断"状态。
    _interruptedConversationIds.add(conversationId);
    _streamAnnouncement = AppStrings.streamAnnounceStopped;
    _markCurrentConversationInterrupted(conversationId);
    notifyListeners();
  }

  void _clearActiveStream() {
    _activeStreamSubscription = null;
    _activeStreamConversationId = null;
    _activeStreamId = null;
  }

  void _finishStreamFailed(
    int conversationId,
    int tempUserId,
    int? assistantMessageId,
    Object error,
  ) {
    final failureText = AppStrings.chatFailureReason(null, error.toString());
    _streamAnnouncement = AppStrings.streamAnnounceFailed;
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
    _liveReasoningSteps.remove(conversationId);
    _clearActiveStream();
    notifyListeners();
  }

  /// 从实时步骤中提取最新一条规划/感知文本（生成完成后静态常驻展示）。
  String? _latestPlanTextFrom(List<ReasoningStepItem> steps) {
    for (final step in steps.reversed) {
      if (step.stepType == 'plan' || step.stepType == 'perceive') {
        final text = step.content.trim();
        if (text.isNotEmpty) {
          return text;
        }
      }
    }
    return null;
  }

  /// 中断后的本地收口：保留已流出的文本，标记生成中断，清空实时轨迹。
  void _markCurrentConversationInterrupted(int conversationId) {
    final current = _conversationStates[conversationId];
    if (current == null) {
      return;
    }
    final messages = List<ConversationMessageItem>.from(current.messages);
    for (var i = messages.length - 1; i >= 0; i--) {
      final item = messages[i];
      if (!item.isUser && item.status == 'streaming') {
        messages[i] = item.copyWith(status: 'completed');
        break;
      }
    }
    _conversationStates[conversationId] = current.copyWith(
      messages: messages,
      isSending: false,
      // 中断不再显示状态文字：stop 按钮复位即为最直接的提示。
      clearStreamStatusLabel: true,
    );
    final plan = _latestPlanTextFrom(
      _liveReasoningSteps[conversationId] ?? const <ReasoningStepItem>[],
    );
    if (plan != null) {
      _finalPlanText[conversationId] = plan;
    }
    _liveReasoningSteps.remove(conversationId);
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

  Future<void> updateUserPreferences() async {
    _userPreferences = await _apiClient.updateUserPreferences();
    notifyListeners();
  }

  Future<void> logout() async {
    _lastKnownUser = _session?.user ?? _lastKnownUser;
    try {
      await _apiClient.logout();
    } catch (_) {
      _apiClient.setAccessToken(null);
    }
    await _unregisterFcmToken();
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
    _liveReasoningSteps.clear();
    _interruptedConversationIds.clear();
    _finalPlanText.clear();
    _stopNotificationPolling();
    _seenNotificationIds.clear();
    SystemNotificationService.instance.resetShownIds();
    _userPreferences = UserPreferences();
    notifyListeners();
  }

  /// FCM 令牌更新回调（由 FirebaseMessagingService 注入）。未登录时缓存，
  /// 登录/会话恢复成功后补注册。
  Future<void> onFcmTokenChanged(String token) async {
    _fcmToken = token;
    if (!isAuthenticated) {
      return;
    }
    try {
      await _apiClient.registerDeviceToken(token, _pushPlatform);
    } catch (_) {
      // 注册失败静默，token 刷新或下次登录重试。
    }
  }

  String get _pushPlatform {
    if (defaultTargetPlatform == TargetPlatform.android) {
      return 'android';
    }
    if (defaultTargetPlatform == TargetPlatform.windows) {
      return 'windows';
    }
    return 'unknown';
  }

  Future<void> _registerPendingFcmToken() async {
    final token = _fcmToken;
    if (token == null || token.isEmpty) {
      return;
    }
    try {
      await _apiClient.registerDeviceToken(token, _pushPlatform);
    } catch (_) {
      // 注册失败静默，下轮 token 刷新或下次登录重试。
    }
  }

  Future<void> _unregisterFcmToken() async {
    final token = _fcmToken;
    if (token == null || token.isEmpty) {
      return;
    }
    try {
      await _apiClient.unregisterDeviceToken(token);
    } catch (_) {
      // 注销失败静默。
    }
    _fcmToken = null;
  }

  void _handleStreamEvent(
    int conversationId,
    ConversationStreamEvent event, {
    required int assistantMessageId,
  }) {
    switch (event.event) {
      case 'run_started':
        _streamAnnouncement = AppStrings.streamAnnounceStarted;
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
      case 'message_reset':
        // 后端反重复重跑：先清空已流式文本，再以新 delta 重建回答。
        final current = _conversationStates[conversationId];
        if (current == null) {
          break;
        }
        final messages = List<ConversationMessageItem>.from(current.messages);
        final index = messages.indexWhere(
          (item) => item.id == assistantMessageId,
        );
        if (index >= 0) {
          messages[index] = messages[index].copyWith(
            status: 'streaming',
            textContent: '',
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
        // 快速完成时立刻捕获 plan 文本，避免横幅在“完成事件→推理卡片事件”
        // 之间闪失（保证 plan 至少持续展示，不闪断）。
        final completedPlan = _latestPlanTextFrom(
          _liveReasoningSteps[conversationId] ?? const <ReasoningStepItem>[],
        );
        if (completedPlan != null) {
          _finalPlanText[conversationId] = completedPlan;
        }
        _setStateFor(
          conversationId,
          _conversationStates[conversationId]!.copyWith(
            clearStreamStatusLabel:
                !_interruptedConversationIds.contains(conversationId),
          ),
        );
        break;
      case 'reasoning_step':
        final stepData = event.data;
        final liveStep = ReasoningStepItem(
          stepType: stepData['step_type'] as String? ?? '',
          label: stepData['label'] as String? ?? '',
          content: stepData['content'] as String? ?? '',
          status: stepData['status'] as String? ?? 'running',
          iteration: stepData['iteration'] as int? ?? 0,
          seq: stepData['seq'] as int? ?? 0,
          degraded: stepData['degraded'] as bool? ?? false,
          planSource: stepData['plan_source'] as String?,
        );
        final steps = List<ReasoningStepItem>.from(
          _liveReasoningSteps[conversationId] ?? <ReasoningStepItem>[],
        );
        final existingIndex = steps.indexWhere(
          (item) =>
              item.stepType == liveStep.stepType &&
              item.iteration == liveStep.iteration,
        );
        if (existingIndex >= 0) {
          steps[existingIndex] = liveStep;
        } else {
          steps.add(liveStep);
        }
        steps.sort((a, b) => a.seq.compareTo(b.seq));
        _liveReasoningSteps[conversationId] = steps;
        break;
      case 'card_snapshot':
        final messageJson =
            event.data['message'] as Map<String, dynamic>? ??
            <String, dynamic>{};
        if (messageJson['message_type'] == 'reasoning_step') {
          // 持久化卡片到达，实时轨迹使命完成：先落袋规划文本，供完成后的静态常驻行。
          final plan = _latestPlanTextFrom(
            _liveReasoningSteps[conversationId] ?? const <ReasoningStepItem>[],
          );
          if (plan != null) {
            _finalPlanText[conversationId] = plan;
          }
          _liveReasoningSteps.remove(conversationId);
        }
        _replaceOrAppendMessage(
          conversationId,
          ConversationMessageItem.fromJson(messageJson),
        );
        break;
      case 'tool_call_started':
        _streamAnnouncement = _toolStatusLabel(
          event.data['tool_name'] as String?,
        );
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
            clearStreamStatusLabel:
                !_interruptedConversationIds.contains(conversationId),
          ),
        );
        break;
      case 'run_cancelled':
        _streamAnnouncement = AppStrings.streamAnnounceStopped;
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
            isSending: false,
            // 中断后不显示状态文字（同 stop 本地收口）。
            clearStreamStatusLabel: true,
          );
          final plan = _latestPlanTextFrom(
            _liveReasoningSteps[conversationId] ?? const <ReasoningStepItem>[],
          );
          if (plan != null) {
            _finalPlanText[conversationId] = plan;
          }
          _liveReasoningSteps.remove(conversationId);
        }
        break;
      case 'run_failed':
        if (event.data['stream_status'] == 'cancelled') {
          // 后端 abort 自行收口（与 run_cancelled 等效），避免显示为失败
          _handleStreamEvent(
            conversationId,
            ConversationStreamEvent(
              event: 'run_cancelled',
              data: const <String, dynamic>{},
            ),
            assistantMessageId: assistantMessageId,
          );
          break;
        }
        _streamAnnouncement = AppStrings.streamAnnounceFailed;
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
            clearStreamStatusLabel:
                !_interruptedConversationIds.contains(conversationId),
          );
          _liveReasoningSteps.remove(conversationId);
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
            clearStreamStatusLabel:
                !_interruptedConversationIds.contains(conversationId),
            isSending: false,
          );
          final plan = _latestPlanTextFrom(
            _liveReasoningSteps[conversationId] ?? const <ReasoningStepItem>[],
          );
          if (plan != null) {
            _finalPlanText[conversationId] = plan;
          }
          _liveReasoningSteps.remove(conversationId);
          // 完成播报：若最后落库的是待确认卡片，则提示需要用户确认。
          _streamAnnouncement =
              _lastMessageNeedsApproval(messages)
              ? AppStrings.streamAnnounceApproval
              : AppStrings.streamAnnounceCompleted;
        }
        break;
      case 'approval_required':
        _streamAnnouncement = AppStrings.streamAnnounceApproval;
        break;
    }
    notifyListeners();
  }

  /// 最新一条消息是否为可操作的待确认卡片（审批语义状态用）。
  bool _lastMessageNeedsApproval(List<ConversationMessageItem> messages) {
    if (messages.isEmpty) {
      return false;
    }
    final last = messages.last;
    if (last.messageType != 'schedule_draft_card' &&
        last.messageType != 'quick_note_preview_card') {
      return false;
    }
    return last.structuredPayload['is_actionable'] == true;
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
