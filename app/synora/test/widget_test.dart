import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_markdown/flutter_markdown.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:synora/src/api_client.dart';
import 'package:synora/src/app.dart';
import 'package:synora/src/app_controller.dart';
import 'package:synora/src/local_session_store.dart';
import 'package:synora/src/models.dart';
import 'package:synora/src/pages/chat/reasoning_trace_card.dart';
import 'package:synora/src/pages/schedule_list_page.dart';
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
  Future<UserPreferences> fetchUserPreferences() async => UserPreferences();

  @override
  Future<UserPreferences> updateUserPreferences() async => UserPreferences();

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

/// 发送/流式对话可 mock 的 ApiClient：sendConversationMessage 立即返回，
/// streamConversation 挂接外部 StreamController（供测试控制事件与结束）。
class _StreamingFakeApiClient extends FakeApiClient {
  _StreamingFakeApiClient({this.streamController, super.conversationMessages});

  final StreamController<ConversationStreamEvent>? streamController;

  @override
  Future<ConversationThreadItem> createConversation({String? title}) async {
    return ConversationThreadItem(
      id: 50,
      title: title ?? '新对话',
      createdAt: DateTime.parse('2026-05-24T00:00:00Z'),
      updatedAt: DateTime.parse('2026-05-24T00:00:00Z'),
      lastMessageAt: DateTime.parse('2026-05-24T00:00:00Z'),
    );
  }

  @override
  Future<ConversationSendAcceptedResult> sendConversationMessage({
    required int conversationId,
    required String textContent,
    required List<int> attachmentIds,
    ConversationTool? selectedTool,
    Map<String, String> context = const <String, String>{},
  }) async {
    return ConversationSendAcceptedResult(
      conversation: ConversationThreadItem(
        id: conversationId,
        title: textContent,
        createdAt: DateTime.parse('2026-05-24T00:00:00Z'),
        updatedAt: DateTime.parse('2026-05-24T00:00:00Z'),
        lastMessageAt: DateTime.parse('2026-05-24T00:00:00Z'),
      ),
      userMessage: ConversationMessageItem(
        id: 100,
        role: 'user',
        messageType: 'text',
        status: 'sent',
        textContent: textContent,
        structuredPayload: const <String, dynamic>{},
        createdAt: DateTime.parse('2026-05-24T00:00:00Z'),
      ),
      assistantMessageId: 200,
      streamId: 'stream-1',
    );
  }

  @override
  Stream<ConversationStreamEvent> streamConversation({
    required int conversationId,
    required String streamId,
  }) {
    final controller = streamController;
    if (controller == null) {
      return const Stream<ConversationStreamEvent>.empty();
    }
    return controller.stream;
  }

  @override
  Future<void> abortConversationStream({
    required int conversationId,
    required String streamId,
  }) async {}

  @override
  Future<ConversationRewindResult> rewindConversationLastTurn(
    int conversationId,
  ) async {
    return ConversationRewindResult(
      conversation: ConversationThreadItem(
        id: conversationId,
        title: '教学安排',
        createdAt: DateTime.parse('2026-05-23T10:00:00Z'),
        updatedAt: DateTime.parse('2026-05-23T10:00:00Z'),
        lastMessageAt: DateTime.parse('2026-05-23T10:00:00Z'),
      ),
      restoredMessage: ConversationMessageItem(
        id: 1,
        role: 'user',
        messageType: 'text',
        status: 'completed',
        textContent: '你好',
        structuredPayload: const <String, dynamic>{},
        createdAt: DateTime.parse('2026-05-23T10:00:00Z'),
      ),
    );
  }
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
  // 外部传入的 controller 随 SynoraApp 拆树时一并 dispose（见 app.dart dispose）。
  return AppController(
    apiClient: FakeApiClient(conversationMessages: conversationMessages),
    sessionStore: FakeLocalSessionStore(),
  );
}

void main() {
  // liveRegion 语义节点专用查找器：按 Semantics widget 的 label 精确定位，
  // 避免与可见文本（如状态行）合并出的同名语义节点混淆。
  Finder announcementSemantics(String label) => find.byWidgetPredicate(
    (widget) => widget is Semantics && widget.properties.label == label,
  );

  testWidgets('默认显示中文登录页', (tester) async {
    final controller = buildTestController();
    await tester.pumpWidget(SynoraApp(controller: controller));
    await tester.pumpAndSettle();
    expect(find.text(AppStrings.appTitle), findsOneWidget);
    expect(find.text(AppStrings.loginButton), findsOneWidget);
    expect(find.text(AppStrings.emailLabel), findsOneWidget);
  });

  testWidgets('登录页默认不预填测试邮箱与密码', (tester) async {
    final controller = buildTestController();
    await tester.pumpWidget(SynoraApp(controller: controller));
    await tester.pumpAndSettle();

    final emailField = tester.widget<TextFormField>(
      find.byType(TextFormField).at(0),
    );
    final passwordField = tester.widget<TextFormField>(
      find.byType(TextFormField).at(1),
    );
    expect(emailField.controller!.text, isEmpty);
    expect(passwordField.controller!.text, isEmpty);
  });

  testWidgets('小屏注册模式与大字体下无溢出', (tester) async {
    tester.view.physicalSize = const Size(320, 480);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.reset);
    tester.platformDispatcher.textScaleFactorTestValue = 1.7;
    addTearDown(tester.platformDispatcher.clearAllTestValues);

    final controller = buildTestController();
    await tester.pumpWidget(SynoraApp(controller: controller));
    await tester.pumpAndSettle();
    // 小屏大字体下切换按钮位于折叠线以下，滚动后可达。
    await tester.ensureVisible(find.text(AppStrings.switchToRegister));
    await tester.pumpAndSettle();
    await tester.tap(find.text(AppStrings.switchToRegister));
    await tester.pumpAndSettle();

    expect(tester.takeException(), isNull);
    expect(find.text(AppStrings.registerButton), findsOneWidget);
    expect(find.text(AppStrings.displayNameLabel), findsOneWidget);
  });

  testWidgets('软键盘弹起时登录字段与提交按钮仍可访问', (tester) async {
    tester.view.physicalSize = const Size(360, 640);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.reset);

    final controller = buildTestController();
    await tester.pumpWidget(SynoraApp(controller: controller));
    await tester.pumpAndSettle();

    tester.view.viewInsets = const FakeViewPadding(bottom: 280);
    await tester.pumpAndSettle();

    // 页面可滚动，提交按钮可滚动到视野内并点击。
    expect(find.byType(SingleChildScrollView), findsOneWidget);
    await tester.ensureVisible(find.text(AppStrings.loginButton));
    await tester.pumpAndSettle();
    await tester.tap(find.text(AppStrings.loginButton));
    await tester.pumpAndSettle();
    expect(tester.takeException(), isNull);
    // 空邮箱提交触发校验错误提示，证明按钮可交互。
    expect(find.text(AppStrings.emailRequired), findsOneWidget);
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

  testWidgets('空输入不显示语音按钮且发送按钮保持可见', (tester) async {
    final controller = buildTestController();
    await controller.login('han.teacher@example.com', 'SynoraMVP123!');
    await tester.pumpWidget(SynoraApp(controller: controller));
    await tester.pumpAndSettle();

    expect(find.byIcon(Icons.mic_none), findsNothing);
    expect(find.byIcon(Icons.send_rounded), findsOneWidget);
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

    expect(find.byTooltip(AppStrings.copy), findsNWidgets(2));
    expect(find.byTooltip(AppStrings.editResend), findsOneWidget);
    // 最后一条 assistant 为已完成文本消息：显示重新生成，不显示重试。
    expect(find.byTooltip(AppStrings.regenerate), findsOneWidget);
    expect(find.byTooltip(AppStrings.retry), findsNothing);
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
    // 审批卡片不显示 assistant 文本类操作。
    expect(find.byTooltip(AppStrings.regenerate), findsNothing);
    expect(find.byTooltip(AppStrings.retry), findsNothing);
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
    // 用户消息与 assistant 消息各有一个复制按钮；复制 assistant 无提示。
    await tester.tap(find.byTooltip(AppStrings.copy).last);
    await tester.pump();
    expect(find.byType(SnackBar), findsNothing);
  });

  testWidgets('Ctrl+Enter 触发发送，单 Enter 仅换行', (tester) async {
    final streamController = StreamController<ConversationStreamEvent>();
    final controller = AppController(
      apiClient: _StreamingFakeApiClient(streamController: streamController),
      sessionStore: FakeLocalSessionStore(),
    );
    await controller.login('han.teacher@example.com', 'SynoraMVP123!');
    await tester.pumpWidget(SynoraApp(controller: controller));
    await tester.pumpAndSettle();

    await tester.enterText(find.byType(TextField).first, '你好');
    await tester.pumpAndSettle();
    // 单 Enter：仅换行，不应触发发送
    await tester.sendKeyEvent(LogicalKeyboardKey.enter);
    await tester.pumpAndSettle();
    expect(controller.isMessageSending, isFalse);

    // Ctrl+Enter：触发发送（发送后有呼吸点无限动画，pumpAndSettle 会超时，改用有限 pump）
    await tester.sendKeyDownEvent(LogicalKeyboardKey.controlLeft);
    await tester.sendKeyEvent(LogicalKeyboardKey.enter);
    await tester.sendKeyUpEvent(LogicalKeyboardKey.controlLeft);
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 300));
    expect(controller.isMessageSending, isTrue);

    await streamController.close();
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 300));
  });

  testWidgets('发送中显示 stop 按钮，点击后停止生成', (tester) async {
    final streamController = StreamController<ConversationStreamEvent>();
    final controller = AppController(
      apiClient: _StreamingFakeApiClient(streamController: streamController),
      sessionStore: FakeLocalSessionStore(),
    );
    await controller.login('han.teacher@example.com', 'SynoraMVP123!');
    await tester.pumpWidget(SynoraApp(controller: controller));
    await tester.pumpAndSettle();

    await tester.enterText(find.byType(TextField).first, '写一段长文');
    await tester.pumpAndSettle();
    await tester.sendKeyDownEvent(LogicalKeyboardKey.controlLeft);
    await tester.sendKeyEvent(LogicalKeyboardKey.enter);
    await tester.sendKeyUpEvent(LogicalKeyboardKey.controlLeft);
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 400));
    await tester.pump();

    expect(find.byIcon(Icons.stop_circle_outlined), findsOneWidget);

    await controller.stopCurrentGeneration();
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 400));
    await tester.pump();
    expect(find.byIcon(Icons.stop_circle_outlined), findsNothing);
    expect(controller.isMessageSending, isFalse);
    // 中断后文本框上方不应残留"生成中断"状态文字。
    expect(controller.streamStatusLabel, isNull);
  });

  testWidgets('长回答流式生成持续跟随底部，上滑后显示回到底部按钮且不再拉回', (tester) async {
    final streamController = StreamController<ConversationStreamEvent>();
    final controller = AppController(
      apiClient: _StreamingFakeApiClient(streamController: streamController),
      sessionStore: FakeLocalSessionStore(),
    );
    await controller.login('han.teacher@example.com', 'SynoraMVP123!');
    await tester.pumpWidget(SynoraApp(controller: controller));
    await tester.pumpAndSettle();

    await tester.enterText(find.byType(TextField).first, '写一段长文');
    await tester.pumpAndSettle();
    await tester.sendKeyDownEvent(LogicalKeyboardKey.controlLeft);
    await tester.sendKeyEvent(LogicalKeyboardKey.enter);
    await tester.sendKeyUpEvent(LogicalKeyboardKey.controlLeft);
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 300));

    // 连续 message_delta 修改同一条 assistant 消息，模拟长回答流式生成。
    const chunk = '这是一段用于撑高消息气泡的流式回答内容，反复累积以模拟长回答。';
    for (var i = 0; i < 24; i++) {
      streamController.add(
        ConversationStreamEvent(
          event: 'message_delta',
          data: <String, dynamic>{'delta': chunk},
        ),
      );
      await tester.pump();
    }
    await tester.pump(const Duration(milliseconds: 300));

    final listScrollable = find
        .descendant(of: find.byType(ListView), matching: find.byType(Scrollable))
        .first;
    ScrollableState listState() =>
        tester.state<ScrollableState>(listScrollable);
    expect(listState().position.pixels, closeTo(listState().position.maxScrollExtent, 1));
    expect(find.byTooltip(AppStrings.jumpToBottom), findsNothing);

    // 用户主动上滑：离开底部后显示"回到底部"按钮。
    await tester.drag(find.byType(ListView), const Offset(0, 600));
    await tester.pumpAndSettle();
    expect(find.byTooltip(AppStrings.jumpToBottom), findsOneWidget);

    // 后续流式 delta 不得强制拉回底部。
    streamController.add(
      ConversationStreamEvent(
        event: 'message_delta',
        data: <String, dynamic>{'delta': chunk},
      ),
    );
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 100));
    final away = tester.state<ScrollableState>(listScrollable).position;
    expect(away.pixels, lessThan(away.maxScrollExtent - 200));

    // 点击"回到底部"：恢复跟随并滚回底部，按钮消失。
    await tester.tap(find.byTooltip(AppStrings.jumpToBottom));
    await tester.pumpAndSettle();
    expect(find.byTooltip(AppStrings.jumpToBottom), findsNothing);
    final back = tester.state<ScrollableState>(listScrollable).position;
    expect(back.pixels, closeTo(back.maxScrollExtent, 1));

    // 收尾事件：message_completed + run_completed，状态复位。
    streamController.add(
      ConversationStreamEvent(
        event: 'message_completed',
        data: <String, dynamic>{
          'message': <String, dynamic>{
            'id': 200,
            'role': 'assistant',
            'message_type': 'text',
            'status': 'completed',
            'text_content': '长文完成',
            'structured_payload': <String, dynamic>{},
            'created_at': '2026-05-24T00:00:01Z',
          },
        },
      ),
    );
    streamController.add(
      ConversationStreamEvent(
        event: 'run_completed',
        data: <String, dynamic>{'stream_id': 'stream-1'},
      ),
    );
    await streamController.close();
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 300));
    expect(controller.isMessageSending, isFalse);
  });

  testWidgets('assistant 文本消息支持复制', (tester) async {
    final clipboardCalls = <MethodCall>[];
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(SystemChannels.platform, (call) async {
          clipboardCalls.add(call);
          return null;
        });
    addTearDown(
      () => TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
          .setMockMethodCallHandler(SystemChannels.platform, null),
    );

    final controller = buildTestController();
    await controller.login('han.teacher@example.com', 'SynoraMVP123!');
    await tester.pumpWidget(SynoraApp(controller: controller));
    await tester.pumpAndSettle();
    await controller.selectConversation(1);
    await tester.pumpAndSettle();

    await tester.tap(find.byTooltip(AppStrings.copy).last);
    await tester.pump();

    final setData = clipboardCalls.lastWhere(
      (call) => call.method == 'Clipboard.setData',
    );
    final text = (setData.arguments as Map<dynamic, dynamic>)['text'] as String;
    expect(text, contains('安排建议'));
  });

  testWidgets('失败回答显示重试，点击后自动重发', (tester) async {
    final streamController = StreamController<ConversationStreamEvent>();
    final controller = AppController(
      apiClient: _StreamingFakeApiClient(
        streamController: streamController,
        conversationMessages: <ConversationMessageItem>[
          ConversationMessageItem(
            id: 1,
            role: 'user',
            messageType: 'text',
            status: 'completed',
            textContent: '你好',
            structuredPayload: const <String, dynamic>{},
            createdAt: DateTime.parse('2026-05-23T10:00:00Z'),
          ),
          ConversationMessageItem(
            id: 2,
            role: 'assistant',
            messageType: 'text',
            status: 'failed',
            textContent: '请求失败',
            structuredPayload: const <String, dynamic>{},
            createdAt: DateTime.parse('2026-05-23T10:00:01Z'),
          ),
        ],
      ),
      sessionStore: FakeLocalSessionStore(),
    );
    await controller.login('han.teacher@example.com', 'SynoraMVP123!');
    await tester.pumpWidget(SynoraApp(controller: controller));
    await tester.pumpAndSettle();
    await controller.selectConversation(1);
    await tester.pumpAndSettle();

    expect(controller.canRetryOrRegenerate(controller.messages.last), isTrue);
    expect(find.byTooltip(AppStrings.retry), findsOneWidget);
    expect(find.byTooltip(AppStrings.regenerate), findsNothing);

    await tester.tap(find.byTooltip(AppStrings.retry));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 400));
    expect(controller.isMessageSending, isTrue);

    // 切走当前会话后不再显示操作，避免误操作其他会话。
    controller.beginDraftConversation();
    await tester.pumpAndSettle();
    expect(find.byTooltip(AppStrings.retry), findsNothing);

    await streamController.close();
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 300));
  });

  testWidgets('已完成回答显示重新生成，点击后重发同一问题', (tester) async {
    final streamController = StreamController<ConversationStreamEvent>();
    final controller = AppController(
      apiClient: _StreamingFakeApiClient(
        streamController: streamController,
        conversationMessages: <ConversationMessageItem>[
          ConversationMessageItem(
            id: 1,
            role: 'user',
            messageType: 'text',
            status: 'completed',
            textContent: '现在几点了',
            structuredPayload: const <String, dynamic>{},
            createdAt: DateTime.parse('2026-05-23T10:00:00Z'),
          ),
          ConversationMessageItem(
            id: 2,
            role: 'assistant',
            messageType: 'text',
            status: 'completed',
            textContent: '现在是 15:30。',
            structuredPayload: const <String, dynamic>{},
            createdAt: DateTime.parse('2026-05-23T10:00:01Z'),
          ),
        ],
      ),
      sessionStore: FakeLocalSessionStore(),
    );
    await controller.login('han.teacher@example.com', 'SynoraMVP123!');
    await tester.pumpWidget(SynoraApp(controller: controller));
    await tester.pumpAndSettle();
    await controller.selectConversation(1);
    await tester.pumpAndSettle();

    expect(find.byTooltip(AppStrings.regenerate), findsOneWidget);
    expect(find.byTooltip(AppStrings.retry), findsNothing);

    await tester.tap(find.byTooltip(AppStrings.regenerate));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 400));
    expect(controller.isMessageSending, isTrue);
    // 撤回后用户问题回到输入框，重发后草稿被清空。
    expect(controller.draftText, isEmpty);

    await streamController.close();
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 300));
  });

  testWidgets('生成中的 assistant 消息不显示复制/重试/重新生成操作', (tester) async {
    final streamController = StreamController<ConversationStreamEvent>();
    final controller = AppController(
      apiClient: _StreamingFakeApiClient(streamController: streamController),
      sessionStore: FakeLocalSessionStore(),
    );
    await controller.login('han.teacher@example.com', 'SynoraMVP123!');
    await tester.pumpWidget(SynoraApp(controller: controller));
    await tester.pumpAndSettle();

    await tester.enterText(find.byType(TextField).first, '你好');
    await tester.pumpAndSettle();
    await tester.sendKeyDownEvent(LogicalKeyboardKey.controlLeft);
    await tester.sendKeyEvent(LogicalKeyboardKey.enter);
    await tester.sendKeyUpEvent(LogicalKeyboardKey.controlLeft);
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 300));

    // 生成中：assistant 为空文本，仅用户消息有复制；无重试/重新生成。
    expect(find.byTooltip(AppStrings.copy), findsOneWidget);
    expect(find.byTooltip(AppStrings.retry), findsNothing);
    expect(find.byTooltip(AppStrings.regenerate), findsNothing);

    await streamController.close();
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 300));
  });

  ConversationMessageItem reasoningStepMessage({
    bool planDegraded = false,
    int id = 3,
  }) {
    return ConversationMessageItem(
      id: id,
      role: 'assistant',
      messageType: 'reasoning_step',
      status: 'completed',
      textContent: '规划: 回答用户 → 行动: 好的 → 观察: 本轮无工具调用 → 反思: 本轮无工具调用，回答完整',
      structuredPayload: <String, dynamic>{
        'steps': <Map<String, dynamic>>[
          <String, dynamic>{
            'seq': 1,
            'step_type': 'plan',
            'label': '规划',
            'content': '回答用户',
            'status': 'completed',
            'iteration': 0,
            'plan_source': planDegraded ? 'llm' : 'deterministic',
            'degraded': planDegraded,
          },
          <String, dynamic>{
            'seq': 2,
            'step_type': 'act',
            'label': '行动',
            'content': '好的',
            'status': 'completed',
            'iteration': 0,
          },
          <String, dynamic>{
            'seq': 3,
            'step_type': 'observe',
            'label': '观察',
            'content': '本轮无工具调用',
            'status': 'completed',
            'iteration': 0,
          },
          <String, dynamic>{
            'seq': 4,
            'step_type': 'reflect',
            'label': '反思',
            'content': '本轮无工具调用，回答完整',
            'status': 'completed',
            'iteration': 0,
          },
        ],
      },
      createdAt: DateTime.parse('2026-05-23T10:00:02Z'),
    );
  }

  testWidgets('推理步骤明细不再渲染', (tester) async {
    final controller = buildTestController(
      conversationMessages: <ConversationMessageItem>[
        ConversationMessageItem(
          id: 1,
          role: 'user',
          messageType: 'text',
          status: 'completed',
          textContent: '你好',
          structuredPayload: const <String, dynamic>{},
          createdAt: DateTime.parse('2026-05-23T10:00:00Z'),
        ),
        ConversationMessageItem(
          id: 2,
          role: 'assistant',
          messageType: 'text',
          status: 'completed',
          textContent: '好的',
          structuredPayload: const <String, dynamic>{},
          createdAt: DateTime.parse('2026-05-23T10:00:01Z'),
        ),
        reasoningStepMessage(),
      ],
    );
    await controller.login('han.teacher@example.com', 'SynoraMVP123!');
    await tester.pumpWidget(SynoraApp(controller: controller));
    await tester.pumpAndSettle();
    await controller.selectConversation(1);
    await tester.pumpAndSettle();

    // 推理步骤明细 UI 已删除：摘要与各步骤标签均不渲染，仅保留消息数据。
    expect(find.textContaining('规划: 回答用户'), findsNothing);
    expect(find.text(AppStrings.reasoningStepAct), findsNothing);
    expect(find.text(AppStrings.reasoningStepReflect), findsNothing);
    expect(tester.takeException(), isNull);
  });

  testWidgets('降级推理步骤消息同样不渲染明细', (tester) async {
    final controller = buildTestController(
      conversationMessages: <ConversationMessageItem>[
        ConversationMessageItem(
          id: 1,
          role: 'user',
          messageType: 'text',
          status: 'completed',
          textContent: '你好',
          structuredPayload: const <String, dynamic>{},
          createdAt: DateTime.parse('2026-05-23T10:00:00Z'),
        ),
        ConversationMessageItem(
          id: 2,
          role: 'assistant',
          messageType: 'text',
          status: 'completed',
          textContent: '好的',
          structuredPayload: const <String, dynamic>{},
          createdAt: DateTime.parse('2026-05-23T10:00:01Z'),
        ),
        reasoningStepMessage(planDegraded: true),
      ],
    );
    await controller.login('han.teacher@example.com', 'SynoraMVP123!');
    await tester.pumpWidget(SynoraApp(controller: controller));
    await tester.pumpAndSettle();
    await controller.selectConversation(1);
    await tester.pumpAndSettle();

    expect(find.text(AppStrings.reasoningDegraded), findsNothing);
    expect(tester.takeException(), isNull);
  });

  testWidgets('失败回答下方有推理卡时仍可重试', (tester) async {
    final controller = buildTestController(
      conversationMessages: <ConversationMessageItem>[
        ConversationMessageItem(
          id: 1,
          role: 'user',
          messageType: 'text',
          status: 'completed',
          textContent: '帮我搜一下 deepseek 现在的 api 价格',
          structuredPayload: const <String, dynamic>{},
          createdAt: DateTime.parse('2026-05-23T10:00:00Z'),
        ),
        ConversationMessageItem(
          id: 2,
          role: 'assistant',
          messageType: 'text',
          status: 'failed',
          textContent: '请求失败',
          structuredPayload: const <String, dynamic>{},
          createdAt: DateTime.parse('2026-05-23T10:00:01Z'),
        ),
        reasoningStepMessage(),
      ],
    );
    await controller.login('han.teacher@example.com', 'SynoraMVP123!');
    await tester.pumpWidget(SynoraApp(controller: controller));
    await tester.pumpAndSettle();
    await controller.selectConversation(1);
    await tester.pumpAndSettle();

    // reasoning_step 卡片不渲染，不应挡住失败回答的重试按钮。
    expect(controller.canRetryOrRegenerate(controller.messages[1]), isTrue);
    expect(find.byTooltip(AppStrings.retry), findsOneWidget);
  });

  testWidgets('减少动态效果开启时流式生成无无限动画', (tester) async {
    tester.platformDispatcher.accessibilityFeaturesTestValue =
        const FakeAccessibilityFeatures(disableAnimations: true);
    addTearDown(tester.platformDispatcher.clearAllTestValues);

    final streamController = StreamController<ConversationStreamEvent>();
    final controller = AppController(
      apiClient: _StreamingFakeApiClient(streamController: streamController),
      sessionStore: FakeLocalSessionStore(),
    );
    await controller.login('han.teacher@example.com', 'SynoraMVP123!');
    await tester.pumpWidget(SynoraApp(controller: controller));
    await tester.pumpAndSettle();

    await tester.enterText(find.byType(TextField).first, '你好');
    await tester.pumpAndSettle();
    await tester.sendKeyDownEvent(LogicalKeyboardKey.controlLeft);
    await tester.sendKeyEvent(LogicalKeyboardKey.enter);
    await tester.sendKeyUpEvent(LogicalKeyboardKey.controlLeft);
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 300));

    expect(controller.isMessageSending, isTrue);
    // 呼吸点/流光动画已按系统设置关闭：pumpAndSettle 可以收敛
    //（若无限动画仍在运行，此处会因超时失败）。
    await tester.pumpAndSettle();

    await streamController.close();
    await tester.pumpAndSettle();
    expect(tester.takeException(), isNull);
  });

  testWidgets('生成状态切换提供语义播报且不逐 token 播报', (tester) async {
    final semantics = tester.ensureSemantics();
    final streamController = StreamController<ConversationStreamEvent>();
    final controller = AppController(
      apiClient: _StreamingFakeApiClient(streamController: streamController),
      sessionStore: FakeLocalSessionStore(),
    );
    await controller.login('han.teacher@example.com', 'SynoraMVP123!');
    await tester.pumpWidget(SynoraApp(controller: controller));
    await tester.pumpAndSettle();

    await tester.enterText(find.byType(TextField).first, '现在几点了');
    await tester.pumpAndSettle();
    await tester.sendKeyDownEvent(LogicalKeyboardKey.controlLeft);
    await tester.sendKeyEvent(LogicalKeyboardKey.enter);
    await tester.sendKeyUpEvent(LogicalKeyboardKey.controlLeft);
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 300));

    // 发送 → 开始生成
    expect(
      announcementSemantics(AppStrings.streamAnnounceSending),
      findsOneWidget,
    );
    streamController.add(
      const ConversationStreamEvent(
        event: 'run_started',
        data: <String, dynamic>{},
      ),
    );
    await tester.pump();
    expect(
      announcementSemantics(AppStrings.streamAnnounceStarted),
      findsOneWidget,
    );

    // 工具运行状态
    streamController.add(
      const ConversationStreamEvent(
        event: 'tool_call_started',
        data: <String, dynamic>{'tool_name': 'get_current_time'},
      ),
    );
    await tester.pump();
    expect(announcementSemantics('正在查询时间'), findsOneWidget);

    // 流式 delta 不改变播报标签（不逐 token 播报）。
    streamController.add(
      const ConversationStreamEvent(
        event: 'message_delta',
        data: <String, dynamic>{'delta': '现在是 15:30。'},
      ),
    );
    await tester.pump();
    expect(announcementSemantics('正在查询时间'), findsOneWidget);

    // 完成播报
    streamController.add(
      const ConversationStreamEvent(
        event: 'run_completed',
        data: <String, dynamic>{'stream_id': 'stream-1'},
      ),
    );
    await tester.pump();
    expect(
      announcementSemantics(AppStrings.streamAnnounceCompleted),
      findsOneWidget,
    );

    await streamController.close();
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 300));
    semantics.dispose();
  });

  testWidgets('失败播报与取消播报语义', (tester) async {
    final semantics = tester.ensureSemantics();
    final streamController = StreamController<ConversationStreamEvent>();
    final controller = AppController(
      apiClient: _StreamingFakeApiClient(streamController: streamController),
      sessionStore: FakeLocalSessionStore(),
    );
    await controller.login('han.teacher@example.com', 'SynoraMVP123!');
    await tester.pumpWidget(SynoraApp(controller: controller));
    await tester.pumpAndSettle();

    await tester.enterText(find.byType(TextField).first, '你好');
    await tester.pumpAndSettle();
    await tester.sendKeyDownEvent(LogicalKeyboardKey.controlLeft);
    await tester.sendKeyEvent(LogicalKeyboardKey.enter);
    await tester.sendKeyUpEvent(LogicalKeyboardKey.controlLeft);
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 300));

    streamController.add(
      const ConversationStreamEvent(
        event: 'run_failed',
        data: <String, dynamic>{'code': 'llm_timeout', 'message': '超时'},
      ),
    );
    await tester.pump();
    expect(
      announcementSemantics(AppStrings.streamAnnounceFailed),
      findsOneWidget,
    );
    await streamController.close();
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 300));
    semantics.dispose();

    // 主动停止 → 取消播报（用全新流，避免上一个已关闭的流使 stop 提前返回）。
    // 先拆掉上一棵树：SynoraApp 的 controller 是 late final，同类型热替换不会生效。
    await tester.pumpWidget(const SizedBox.shrink());
    await tester.pump();
    final stopSemantics = tester.ensureSemantics();
    final stopStream = StreamController<ConversationStreamEvent>();
    final stopController = AppController(
      apiClient: _StreamingFakeApiClient(streamController: stopStream),
      sessionStore: FakeLocalSessionStore(),
    );
    await stopController.login('han.teacher@example.com', 'SynoraMVP123!');
    await tester.pumpWidget(SynoraApp(controller: stopController));
    await tester.pumpAndSettle();
    await tester.enterText(find.byType(TextField).first, '再问一次');
    await tester.pumpAndSettle();
    await tester.sendKeyDownEvent(LogicalKeyboardKey.controlLeft);
    await tester.sendKeyEvent(LogicalKeyboardKey.enter);
    await tester.sendKeyUpEvent(LogicalKeyboardKey.controlLeft);
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 300));
    await stopController.stopCurrentGeneration();
    await tester.pump();
    expect(
      announcementSemantics(AppStrings.streamAnnounceStopped),
      findsOneWidget,
    );
    // 订阅已被 stop 取消：单订阅流的 close() 不会再完成，直接拆树收尾。
    await tester.pumpWidget(const SizedBox.shrink());
    await tester.pump();
    stopStream.close();
    stopSemantics.dispose();
  });

  testWidgets('大字体下聊天页与推理步骤消息无溢出', (tester) async {
    tester.platformDispatcher.textScaleFactorTestValue = 1.6;
    addTearDown(tester.platformDispatcher.clearAllTestValues);

    final controller = buildTestController(
      conversationMessages: <ConversationMessageItem>[
        ConversationMessageItem(
          id: 1,
          role: 'user',
          messageType: 'text',
          status: 'completed',
          textContent: '你好',
          structuredPayload: const <String, dynamic>{},
          createdAt: DateTime.parse('2026-05-23T10:00:00Z'),
        ),
        ConversationMessageItem(
          id: 2,
          role: 'assistant',
          messageType: 'text',
          status: 'completed',
          textContent: '好的',
          structuredPayload: const <String, dynamic>{},
          createdAt: DateTime.parse('2026-05-23T10:00:01Z'),
        ),
        reasoningStepMessage(),
      ],
    );
    await controller.login('han.teacher@example.com', 'SynoraMVP123!');
    await tester.pumpWidget(SynoraApp(controller: controller));
    await tester.pumpAndSettle();
    await controller.selectConversation(1);
    await tester.pumpAndSettle();
    expect(tester.takeException(), isNull);
    expect(find.text(AppStrings.reasoningStepReflect), findsNothing);
  });

  testWidgets('发送后呼吸点独立显示，plan 到达切换为计划行，act 文本进入气泡', (tester) async {
    final streamController = StreamController<ConversationStreamEvent>();
    final controller = AppController(
      apiClient: _StreamingFakeApiClient(streamController: streamController),
      sessionStore: FakeLocalSessionStore(),
    );
    await controller.login('han.teacher@example.com', 'SynoraMVP123!');
    await tester.pumpWidget(SynoraApp(controller: controller));
    await tester.pumpAndSettle();

    await tester.enterText(find.byType(TextField).first, '现在几点了');
    await tester.pumpAndSettle();
    await tester.sendKeyDownEvent(LogicalKeyboardKey.controlLeft);
    await tester.sendKeyEvent(LogicalKeyboardKey.enter);
    await tester.sendKeyUpEvent(LogicalKeyboardKey.controlLeft);
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 300));

    // AI 未返回：只在气泡外显示呼吸点，assistant 气泡本身不渲染。
    expect(find.byType(BreathingDot), findsOneWidget);
    expect(find.byType(MarkdownBody), findsNothing);

    // plan 到达：关闭呼吸点，显示计划行。
    streamController.add(
      const ConversationStreamEvent(
        event: 'reasoning_step',
        data: <String, dynamic>{
          'seq': 1,
          'step_type': 'plan',
          'label': '规划',
          'content': '回答用户问题',
          'status': 'completed',
          'iteration': 0,
          'plan_source': 'deterministic',
        },
      ),
    );
    await tester.pump();
    expect(find.byType(BreathingDot), findsNothing);
    expect(find.text('回答用户问题'), findsOneWidget);

    // act 产出文本：进入气泡展示。
    streamController.add(
      const ConversationStreamEvent(
        event: 'message_delta',
        data: <String, dynamic>{'delta': '现在是 15:30。'},
      ),
    );
    await tester.pump();
    expect(find.byType(MarkdownBody), findsOneWidget);
    expect(find.textContaining('现在是 15:30。'), findsOneWidget);

    await streamController.close();
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 300));
  });

  testWidgets('message_reset 事件清空已流式文本（反重复重跑）', (tester) async {
    final streamController = StreamController<ConversationStreamEvent>();
    final controller = AppController(
      apiClient: _StreamingFakeApiClient(streamController: streamController),
      sessionStore: FakeLocalSessionStore(),
    );
    await controller.login('han.teacher@example.com', 'SynoraMVP123!');
    await tester.pumpWidget(SynoraApp(controller: controller));
    await tester.pumpAndSettle();

    await tester.enterText(find.byType(TextField).first, '你有什么工具');
    await tester.pumpAndSettle();
    await tester.sendKeyDownEvent(LogicalKeyboardKey.controlLeft);
    await tester.sendKeyEvent(LogicalKeyboardKey.enter);
    await tester.sendKeyUpEvent(LogicalKeyboardKey.controlLeft);
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 300));

    streamController.add(
      const ConversationStreamEvent(
        event: 'message_delta',
        data: <String, dynamic>{'delta': '我无法提供我的系统提示词。'},
      ),
    );
    await tester.pump();
    expect(controller.messages.last.textContent, '我无法提供我的系统提示词。');

    streamController.add(
      const ConversationStreamEvent(
        event: 'message_reset',
        data: <String, dynamic>{},
      ),
    );
    await tester.pump();
    expect(controller.messages.last.textContent, isEmpty);

    streamController.add(
      const ConversationStreamEvent(
        event: 'message_delta',
        data: <String, dynamic>{'delta': '我可以帮你安排日程、速记和联网搜索。'},
      ),
    );
    await tester.pump();
    expect(controller.messages.last.textContent, '我可以帮你安排日程、速记和联网搜索。');

    await streamController.close();
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 300));
  });

  testWidgets('快速完成时 plan 横幅持续显示不闪失', (tester) async {
    final streamController = StreamController<ConversationStreamEvent>();
    final controller = AppController(
      apiClient: _StreamingFakeApiClient(streamController: streamController),
      sessionStore: FakeLocalSessionStore(),
    );
    await controller.login('han.teacher@example.com', 'SynoraMVP123!');
    await tester.pumpWidget(SynoraApp(controller: controller));
    await tester.pumpAndSettle();

    await tester.enterText(find.byType(TextField).first, '现在几点了');
    await tester.pumpAndSettle();
    await tester.sendKeyDownEvent(LogicalKeyboardKey.controlLeft);
    await tester.sendKeyEvent(LogicalKeyboardKey.enter);
    await tester.sendKeyUpEvent(LogicalKeyboardKey.controlLeft);
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 300));

    // plan 到达
    streamController.add(
      const ConversationStreamEvent(
        event: 'reasoning_step',
        data: <String, dynamic>{
          'seq': 1,
          'step_type': 'plan',
          'label': '规划',
          'content': '回答用户问题',
          'status': 'completed',
          'iteration': 0,
          'plan_source': 'deterministic',
        },
      ),
    );
    await tester.pump();
    expect(find.text('回答用户问题'), findsOneWidget);

    // 极速完成：delta → message_completed（完成事件早于推理卡事件）
    streamController.add(
      const ConversationStreamEvent(
        event: 'message_delta',
        data: <String, dynamic>{'delta': '现在是 15:30。'},
      ),
    );
    await tester.pump();
    streamController.add(
      ConversationStreamEvent(
        event: 'message_completed',
        data: <String, dynamic>{
          'message': <String, dynamic>{
            'id': 200,
            'role': 'assistant',
            'message_type': 'text',
            'status': 'completed',
            'text_content': '现在是 15:30。',
            'structured_payload': <String, dynamic>{},
            'created_at': '2026-05-24T00:00:01Z',
          },
        },
      ),
    );
    await tester.pump();
    // 完成瞬间 plan 仍显示（不闪失）
    expect(find.text('回答用户问题'), findsOneWidget);

    // 推理卡快照插入后 plan 依旧显示
    streamController.add(
      ConversationStreamEvent(
        event: 'card_snapshot',
        data: <String, dynamic>{
          'message': <String, dynamic>{
            'id': 300,
            'role': 'assistant',
            'message_type': 'reasoning_step',
            'status': 'completed',
            'text_content': '规划: 回答用户问题 → 行动: 现在是 15:30。',
            'structured_payload': <String, dynamic>{
              'steps': <Map<String, dynamic>>[
                <String, dynamic>{
                  'seq': 1,
                  'step_type': 'plan',
                  'label': '规划',
                  'content': '回答用户问题',
                  'status': 'completed',
                  'iteration': 0,
                  'plan_source': 'deterministic',
                },
              ],
              'summary': '规划: 回答用户问题 → 行动: 现在是 15:30。',
            },
            'created_at': '2026-05-24T00:00:02Z',
          },
        },
      ),
    );
    await tester.pump();
    expect(find.text('回答用户问题'), findsOneWidget);

    streamController.add(
      const ConversationStreamEvent(
        event: 'run_completed',
        data: <String, dynamic>{'stream_id': 'stream-1'},
      ),
    );
    await tester.pump();
    // 再模拟等待超过 2 秒：plan 仍持续展示（满足“至少显示 2s”）。
    await tester.pump(const Duration(seconds: 2));
    expect(find.text('回答用户问题'), findsOneWidget);

    await streamController.close();
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 300));
  });

  testWidgets('assistant 气泡首次出现播放入场动画', (tester) async {
    final streamController = StreamController<ConversationStreamEvent>();
    final controller = AppController(
      apiClient: _StreamingFakeApiClient(streamController: streamController),
      sessionStore: FakeLocalSessionStore(),
    );
    await controller.login('han.teacher@example.com', 'SynoraMVP123!');
    await tester.pumpWidget(SynoraApp(controller: controller));
    await tester.pumpAndSettle();

    await tester.enterText(find.byType(TextField).first, '现在几点了');
    await tester.pumpAndSettle();
    await tester.sendKeyDownEvent(LogicalKeyboardKey.controlLeft);
    await tester.sendKeyEvent(LogicalKeyboardKey.enter);
    await tester.sendKeyUpEvent(LogicalKeyboardKey.controlLeft);
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 300));

    // AI 未返回：只有呼吸点，无气泡动画容器。
    const entranceKey = ValueKey<String>('assistant-bubble-entrance-200');
    expect(find.byKey(entranceKey), findsNothing);

    // 首个 delta 到达：气泡首次出现并播放一次性入场动画。
    streamController.add(
      const ConversationStreamEvent(
        event: 'message_delta',
        data: <String, dynamic>{'delta': '现在是 15:30。'},
      ),
    );
    await tester.pump();
    expect(find.byKey(entranceKey), findsOneWidget);
    expect(find.byType(MarkdownBody), findsOneWidget);

    // 动画结束后气泡正常保留，继续追加 delta 不再重播。
    await tester.pump(const Duration(milliseconds: 400));
    streamController.add(
      const ConversationStreamEvent(
        event: 'message_delta',
        data: <String, dynamic>{'delta': ' 祝你愉快。'},
      ),
    );
    await tester.pump();
    // 流式通知经 AnimatedBuilder→ListView 逐帧传播，多 pump 一帧后断言。
    await tester.pump();
    expect(find.byKey(entranceKey), findsOneWidget);
    expect(find.textContaining('祝你愉快'), findsOneWidget);

    await streamController.close();
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 300));
  });

  testWidgets('宽屏下侧边栏常驻且聊天页无溢出', (tester) async {
    tester.view.physicalSize = const Size(1200, 800);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.reset);

    final controller = buildTestController();
    await controller.login('han.teacher@example.com', 'SynoraMVP123!');
    await tester.pumpWidget(SynoraApp(controller: controller));
    await tester.pumpAndSettle();

    // >=1000px：侧边栏常驻，无抽屉入口 AppBar 菜单按钮。
    expect(find.text(AppStrings.conversationHistory), findsOneWidget);
    expect(find.byTooltip('打开侧边栏'), findsNothing);
    await controller.selectConversation(1);
    await tester.pumpAndSettle();
    expect(find.byType(MarkdownBody), findsWidgets);
    expect(tester.takeException(), isNull);
  });

  testWidgets('平板宽度聊天页无溢出且可打开抽屉', (tester) async {
    tester.view.physicalSize = const Size(800, 1024);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.reset);

    final controller = buildTestController();
    await controller.login('han.teacher@example.com', 'SynoraMVP123!');
    await tester.pumpWidget(SynoraApp(controller: controller));
    await tester.pumpAndSettle();

    expect(find.byTooltip('打开侧边栏'), findsOneWidget);
    await tester.tap(find.byTooltip('打开侧边栏'));
    await tester.pumpAndSettle();
    expect(find.text(AppStrings.mySchedules), findsOneWidget);
    await controller.selectConversation(1);
    await tester.pumpAndSettle();
    expect(tester.takeException(), isNull);
  });

  testWidgets('日程月历在 PC 宽屏下格子高度固定且日历限宽', (tester) async {
    tester.view.physicalSize = const Size(1400, 900);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.reset);

    final controller = buildTestController();
    await controller.login('han.teacher@example.com', 'SynoraMVP123!');
    await tester.pumpWidget(
      MaterialApp(home: ScheduleListPage(controller: controller)),
    );
    await tester.pumpAndSettle();

    final grid = tester.widget<GridView>(find.byType(GridView));
    final delegate =
        grid.gridDelegate as SliverGridDelegateWithFixedCrossAxisCount;
    expect(delegate.mainAxisExtent, 80);
    final widthBox = tester.widget<ConstrainedBox>(
      find
          .ancestor(
            of: find.byType(GridView),
            matching: find.byType(ConstrainedBox),
          )
          .first,
    );
    expect(widthBox.constraints.maxWidth, 720);
    expect(tester.takeException(), isNull);

    // 页面未走 SynoraApp 树：手动释放 controller，避免通知轮询 Timer 残留。
    controller.dispose();
    await tester.pumpWidget(const SizedBox.shrink());
    await tester.pump();
  });
}
