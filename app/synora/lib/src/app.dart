import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';

import 'app_controller.dart';
import 'app_scope.dart';
import 'firebase_messaging_service.dart';
import 'pages/chat_home_page.dart';
import 'pages/login_page.dart';
import 'pages/notifications_page.dart';
import 'strings.dart';
import 'system_notification_service.dart';

class SynoraApp extends StatefulWidget {
  const SynoraApp({super.key, this.controller});

  final AppController? controller;

  @override
  State<SynoraApp> createState() => _SynoraAppState();
}

class _SynoraAppState extends State<SynoraApp> {
  late final AppController _controller;
  final GlobalKey<NavigatorState> _navigatorKey = GlobalKey<NavigatorState>();

  @override
  void initState() {
    super.initState();
    _controller = widget.controller ?? AppController();
    unawaited(_initSystemNotifications());
  }

  Future<void> _initSystemNotifications() async {
    await SystemNotificationService.instance.init(
      onTap: (notificationId) {
        final navigator = _navigatorKey.currentState;
        if (navigator == null) {
          return;
        }
        navigator.push(
          MaterialPageRoute<void>(
            builder: (_) => NotificationsPage(controller: _controller),
          ),
        );
      },
    );
    // Android 13+ 运行时通知权限；用户在设置里拒绝后不阻塞主流程。
    await SystemNotificationService.instance.requestNotificationsPermission();
    // FCM：安卓前台推送转本地通知；彻底关闭时由系统托盘展示。Windows 不支持 FCM，
    // 仅走轮询。未配置 google-services 时静默降级。
    FirebaseMessagingService.instance.onTokenChanged =
        _controller.onFcmTokenChanged;
    await FirebaseMessagingService.instance.init();
  }

  @override
  void dispose() {
    // 生产代码从不外部传入 controller（main.dart 用 const SynoraApp()），
    // 测试传入的外部 controller 也随 widget 树拆树时一并释放，否则通知轮询
    // Timer 残留导致 flutter_test "Timer is still pending" 失败。
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final colorScheme = ColorScheme.fromSeed(
      seedColor: const Color(0xFF1B6F5F),
      brightness: Brightness.light,
    );
    return AppScope(
      controller: _controller,
      child: AnimatedBuilder(
        animation: _controller,
        builder: (context, _) {
          return MaterialApp(
            navigatorKey: _navigatorKey,
            title: AppStrings.appTitle,
            debugShowCheckedModeBanner: false,
            locale: const Locale('zh', 'CN'),
            supportedLocales: const [Locale('zh', 'CN')],
            localizationsDelegates: GlobalMaterialLocalizations.delegates,
            theme: ThemeData(
              colorScheme: colorScheme,
              useMaterial3: true,
              scaffoldBackgroundColor: const Color(0xFFF8FBFA),
              appBarTheme: const AppBarTheme(
                centerTitle: false,
                backgroundColor: Colors.white,
                foregroundColor: Color(0xFF173C35),
                elevation: 0,
              ),
              inputDecorationTheme: InputDecorationTheme(
                filled: true,
                fillColor: Colors.white,
                border: OutlineInputBorder(borderRadius: BorderRadius.circular(16)),
                enabledBorder: OutlineInputBorder(
                  borderRadius: BorderRadius.circular(16),
                  borderSide: const BorderSide(color: Color(0xFFD7E5E1)),
                ),
              ),
              cardTheme: CardThemeData(
                color: Colors.white,
                elevation: 1,
                shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(20)),
              ),
            ),
            home: _controller.isRestoringSession
                ? const _SessionRestorePage()
                : _controller.isAuthenticated
                ? ChatHomePage(controller: _controller)
                : LoginPage(controller: _controller),
          );
        },
      ),
    );
  }
}

class _SessionRestorePage extends StatelessWidget {
  const _SessionRestorePage();

  @override
  Widget build(BuildContext context) {
    return const Scaffold(
      body: Center(
        child: CircularProgressIndicator(),
      ),
    );
  }
}
