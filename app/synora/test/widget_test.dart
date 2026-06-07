import 'package:flutter/material.dart';
import 'package:flutter_markdown/flutter_markdown.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:synora/src/api_client.dart';
import 'package:synora/src/app.dart';
import 'package:synora/src/app_controller.dart';
import 'package:synora/src/local_session_store.dart';
import 'package:synora/src/models.dart';
import 'package:synora/src/strings.dart';

class FakeApiClient extends ApiClient {
  FakeApiClient({List<ConversationMessageItem>? conversationMessages})
    : _conversationMessages =
          conversationMessages ?? _defaultConversationMessages(),
      super(baseUrl: 'http://localhost:8000');

  final List<ConversationMessageItem> _conversationMessages;

  static List<ConversationMessageItem> _defaultConversationMessages() =>
      <ConversationMessageItem>[
        ConversationMessageItem(
          id: 1,
          role: 'user',
          messageType: 'text',
          status: 'completed',
          textContent: '帮我看一下明天下午安排',
          structuredPayload: const <String, dynamic>{},
          createdAt: DateTime.parse('2026-05-23T10:00:00Z'),
        ),
        ConversationMessageItem(
          id: 2,
          role: 'assistant',
          messageType: 'text',
          status: 'completed',
          textContent: '# 安排建议\n\n- 先确认开会时间\n- 再补充地点',
          structuredPayload: const <String, dynamic>{},
          createdAt: DateTime.parse('2026-05-23T10:00:01Z'),
        ),
      ];

  @override
  Future<SessionInfo> login(String email, String password) async {
    return SessionInfo(
      accessToken: 'token',
      expiresAt: DateTime.parse('2026-05-24T00:00:00Z'),
      user: UserProfile(id: 1, email: email, displayName: '韩老师'),
    );
  }

  @override
  Future<List<ScheduleItem>> fetchSchedules({String? query}) async =>
      <ScheduleItem>[];

  @override
  Future<List<QuickNoteItem>> fetchQuickNotes({
    String? tag,
    String? query,
  }) async => <QuickNoteItem>[];

  @override
  Future<List<NotificationItem>> fetchNotifications() async =>
      <NotificationItem>[];

  @override
  Future<CurrentSessionInfo> fetchCurrentSession() async {
    return CurrentSessionInfo(
      expiresAt: DateTime.parse('2026-05-24T00:00:00Z'),
      user: UserProfile(
        id: 1,
        email: 'han.teacher@example.com',
        displayName: '韩老师',
      ),
    );
  }

  @override
  Future<UserPreferences> fetchUserPreferences() async =>
      UserPreferences(wecomRobotWebhook: null);

  @override
  Future<UserPreferences> updateUserPreferences({
    required String? wecomRobotWebhook,
  }) async => UserPreferences(wecomRobotWebhook: wecomRobotWebhook);

  @override
  Future<MemoryListResult> fetchMemory() async {
    return MemoryListResult(
      summary: '韩老师通常希望提前一天提醒，晚上十点后不要再安排会议。',
      items: <MemoryItem>[
        MemoryItem(
          id: 1,
          memoryType: 'preference',
          title: '提醒偏好',
          content: '通常提前一天提醒',
          sourceKind: 'conversation_message',
          isActive: true,
          updatedAt: DateTime.parse('2026-05-23T10:00:00Z'),
        ),
      ],
    );
  }

  @override
  Future<List<ConversationThreadItem>> fetchConversations() async {
    return <ConversationThreadItem>[
      ConversationThreadItem(
        id: 1,
        title: '教学安排',
        createdAt: DateTime.parse('2026-05-23T10:00:00Z'),
        updatedAt: DateTime.parse('2026-05-23T10:00:00Z'),
        lastMessageAt: DateTime.parse('2026-05-23T10:00:00Z'),
      ),
    ];
  }

  @override
  Future<(ConversationThreadItem, List<ConversationMessageItem>)>
  fetchConversationMessages(int conversationId) async {
    return (
      ConversationThreadItem(
        id: conversationId,
        title: '教学安排',
        createdAt: DateTime.parse('2026-05-23T10:00:00Z'),
        updatedAt: DateTime.parse('2026-05-23T10:00:00Z'),
        lastMessageAt: DateTime.parse('2026-05-23T10:00:00Z'),
      ),
      List<ConversationMessageItem>.from(_conversationMessages),
    );
  }

  @override
  Future<ConversationThreadItem> renameConversation({
    required int conversationId,
    required String title,
  }) async {
    return ConversationThreadItem(
      id: conversationId,
      title: title,
      createdAt: DateTime.parse('2026-05-23T10:00:00Z'),
      updatedAt: DateTime.parse('2026-05-23T10:00:00Z'),
      lastMessageAt: DateTime.parse('2026-05-23T10:00:00Z'),
    );
  }

  @override
  Future<void> deleteConversation(int conversationId) async {}
}

class FakeLocalSessionStore extends LocalSessionStore {
  FakeLocalSessionStore();

  SessionInfo? _session;
  UserProfile? _lastKnownUser;

  @override
  Future<PersistedSessionSnapshot?> readSession() async {
    final session = _session;
    if (session == null) {
      return null;
    }
    return PersistedSessionSnapshot(
      accessToken: session.accessToken,
      expiresAt: session.expiresAt,
      user: session.user,
    );
  }

  @override
  Future<UserProfile?> readLastKnownUser() async => _lastKnownUser;

  @override
  Future<void> saveSession(SessionInfo session) async {
    _session = session;
    _lastKnownUser = session.user;
  }

  @override
  Future<void> clearSessionToken() async {
    _session = null;
  }
}

AppController buildTestController({
  List<ConversationMessageItem>? conversationMessages,
}) {
  return AppController(
    apiClient: FakeApiClient(conversationMessages: conversationMessages),
    sessionStore: FakeLocalSessionStore(),
  );
}

void main() {
  testWidgets('默认显示中文登录页', (tester) async {
    final controller = buildTestController();
    await tester.pumpWidget(SynoraApp(controller: controller));
    await tester.pumpAndSettle();
    expect(find.text(AppStrings.appTitle), findsOneWidget);
    expect(find.text(AppStrings.loginButton), findsOneWidget);
    expect(find.text(AppStrings.emailLabel), findsOneWidget);
  });

  testWidgets('登录后进入本地新对话草稿并可打开侧边栏', (tester) async {
    final controller = buildTestController();
    await controller.login('han.teacher@example.com', 'SynoraMVP123!');
    await tester.pumpWidget(SynoraApp(controller: controller));
    await tester.pumpAndSettle();

    expect(find.text(AppStrings.emptyConversation), findsOneWidget);
    await tester.tap(find.byTooltip('打开侧边栏'));
    await tester.pumpAndSettle();
    expect(find.text(AppStrings.mySchedules), findsOneWidget);
    expect(find.text(AppStrings.conversationHistory), findsOneWidget);
  });

  testWidgets('空输入显示语音按钮，输入后切换为发送按钮', (tester) async {
    final controller = buildTestController();
    await controller.login('han.teacher@example.com', 'SynoraMVP123!');
    await tester.pumpWidget(SynoraApp(controller: controller));
    await tester.pumpAndSettle();

    expect(find.byIcon(Icons.mic_none), findsOneWidget);
    await tester.enterText(find.byType(TextField).first, '明天下午三点开会');
    await tester.pumpAndSettle();
    expect(find.byIcon(Icons.send_rounded), findsOneWidget);
  });

  testWidgets('设置页可进入记忆管理页面', (tester) async {
    final controller = buildTestController();
    await controller.login('han.teacher@example.com', 'SynoraMVP123!');
    await tester.pumpWidget(SynoraApp(controller: controller));
    await tester.pumpAndSettle();

    await tester.tap(find.byTooltip('打开侧边栏'));
    await tester.pumpAndSettle();
    await tester.tap(find.byIcon(Icons.settings_outlined));
    await tester.pumpAndSettle();
    expect(find.text(AppStrings.memoryManagement), findsOneWidget);
  });

  testWidgets('历史会话只允许最后一条用户消息显示编辑按钮', (tester) async {
    final controller = buildTestController();
    await controller.login('han.teacher@example.com', 'SynoraMVP123!');
    await tester.pumpWidget(SynoraApp(controller: controller));
    await tester.pumpAndSettle();

    await tester.tap(find.byTooltip('打开侧边栏'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('教学安排'));
    await tester.pumpAndSettle();

    expect(find.byTooltip(AppStrings.copy), findsOneWidget);
    expect(find.byTooltip(AppStrings.editResend), findsOneWidget);
  });

  testWidgets('下方有卡片的最后一条用户消息不显示编辑按钮', (tester) async {
    final controller = buildTestController(
      conversationMessages: <ConversationMessageItem>[
        ConversationMessageItem(
          id: 1,
          role: 'user',
          messageType: 'text',
          status: 'completed',
          textContent: '帮我创建明天下午的理发日程',
          structuredPayload: const <String, dynamic>{},
          createdAt: DateTime.parse('2026-05-23T10:00:00Z'),
        ),
        ConversationMessageItem(
          id: 2,
          role: 'assistant',
          messageType: 'quick_note_preview_card',
          status: 'completed',
          textContent: null,
          structuredPayload: const <String, dynamic>{
            'lifecycle_status': 'approval_pending',
            'normalized_content': '记录明天下午理发安排',
            'preview_tags': <String>['生活'],
            'evidence_digest': <String>['明天下午理发'],
          },
          createdAt: DateTime.parse('2026-05-23T10:00:01Z'),
        ),
      ],
    );
    await controller.login('han.teacher@example.com', 'SynoraMVP123!');
    await tester.pumpWidget(SynoraApp(controller: controller));
    await tester.pumpAndSettle();
    await controller.selectConversation(1);
    await tester.pumpAndSettle();

    expect(controller.messages, hasLength(2));
    expect(controller.messages.first.isUser, isTrue);
    expect(controller.canEditMessage(controller.messages.first), isFalse);
    expect(find.byTooltip(AppStrings.editResend), findsNothing);
  });

  testWidgets('点击会话菜单使用 context menu 而不是底部菜单', (tester) async {
    final controller = buildTestController();
    await controller.login('han.teacher@example.com', 'SynoraMVP123!');
    await tester.pumpWidget(SynoraApp(controller: controller));
    await tester.pumpAndSettle();

    await tester.tap(find.byTooltip('打开侧边栏'));
    await tester.pumpAndSettle();
    await tester.tap(find.byTooltip(AppStrings.conversationMenu));
    await tester.pumpAndSettle();

    expect(find.text(AppStrings.renameConversation), findsOneWidget);
    expect(find.text(AppStrings.deleteConversation), findsOneWidget);
    expect(find.byType(BottomSheet), findsNothing);
  });

  testWidgets('assistant 消息支持 Markdown 渲染且复制无提示', (tester) async {
    final controller = buildTestController();
    await controller.login('han.teacher@example.com', 'SynoraMVP123!');
    await tester.pumpWidget(SynoraApp(controller: controller));
    await tester.pumpAndSettle();

    await tester.tap(find.byTooltip('打开侧边栏'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('教学安排'));
    await tester.pumpAndSettle();

    expect(find.byType(MarkdownBody), findsWidgets);
    await tester.tap(find.byTooltip(AppStrings.copy));
    await tester.pump();
    expect(find.byType(SnackBar), findsNothing);
  });
}
