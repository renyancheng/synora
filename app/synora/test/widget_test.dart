import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:synora/src/api_client.dart';
import 'package:synora/src/app.dart';
import 'package:synora/src/app_controller.dart';
import 'package:synora/src/models.dart';
import 'package:synora/src/strings.dart';


class FakeApiClient extends ApiClient {
  FakeApiClient() : super(baseUrl: 'http://localhost:8000');

  @override
  Future<SessionInfo> login(String email, String password) async {
    return SessionInfo(
      accessToken: 'token',
      expiresAt: DateTime.parse('2026-05-24T00:00:00Z'),
      user: UserProfile(
        id: 1,
        email: email,
        displayName: '韩老师',
      ),
    );
  }

  @override
  Future<List<ScheduleItem>> fetchSchedules() async => <ScheduleItem>[];

  @override
  Future<List<QuickNoteItem>> fetchQuickNotes() async => <QuickNoteItem>[];

  @override
  Future<List<NotificationItem>> fetchNotifications() async => <NotificationItem>[];

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
  Future<(ConversationThreadItem, List<ConversationMessageItem>)> fetchConversationMessages(int conversationId) async {
    return (
      ConversationThreadItem(
        id: conversationId,
        title: '教学安排',
        createdAt: DateTime.parse('2026-05-23T10:00:00Z'),
        updatedAt: DateTime.parse('2026-05-23T10:00:00Z'),
        lastMessageAt: DateTime.parse('2026-05-23T10:00:00Z'),
      ),
      <ConversationMessageItem>[
        ConversationMessageItem(
          id: 1,
          role: 'assistant',
          messageType: 'text',
          status: 'completed',
          textContent: '你好，我在这里。',
          structuredPayload: const <String, dynamic>{},
          createdAt: DateTime.parse('2026-05-23T10:00:00Z'),
        ),
      ],
    );
  }
}


void main() {
  testWidgets('默认显示中文登录页', (tester) async {
    await tester.pumpWidget(const SynoraApp());

    expect(find.text(AppStrings.appTitle), findsOneWidget);
    expect(find.text(AppStrings.loginButton), findsOneWidget);
    expect(find.text(AppStrings.emailLabel), findsOneWidget);
  });

  testWidgets('登录后进入聊天主页并可打开侧边栏和语音占位', (tester) async {
    final controller = AppController(apiClient: FakeApiClient());
    await controller.login('han.teacher@example.com', 'SynoraMVP123!');

    await tester.pumpWidget(SynoraApp(controller: controller));
    await tester.pumpAndSettle();

    expect(find.text(AppStrings.appTitle), findsOneWidget);
    expect(find.text('你好，我在这里。'), findsOneWidget);

    await tester.tap(find.byIcon(Icons.menu));
    await tester.pumpAndSettle();

    expect(find.text(AppStrings.mySchedules), findsOneWidget);
    expect(find.text(AppStrings.myQuickNotes), findsOneWidget);
    expect(find.text(AppStrings.conversationHistory), findsOneWidget);
    expect(find.text(AppStrings.newConversation), findsOneWidget);

    Navigator.of(tester.element(find.text(AppStrings.mySchedules))).pop();
    await tester.pumpAndSettle();

    await tester.tap(find.byIcon(Icons.mic_none));
    await tester.pump();

    expect(find.text(AppStrings.voiceComingSoon), findsOneWidget);
  });
}
