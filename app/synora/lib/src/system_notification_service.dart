import 'dart:io';

import 'package:flutter_local_notifications/flutter_local_notifications.dart';

/// 系统级通知封装：Windows 桌面 + Android 本地通知。
///
/// 后端 system channel 审计由 app_controller 定时轮询拉取，到点后调用
/// [show] 弹本地通知；应用彻底关闭时的推送由 FCM 补充（见 FCM 接入）。
class SystemNotificationService {
  SystemNotificationService._();

  static final SystemNotificationService instance =
      SystemNotificationService._();

  final FlutterLocalNotificationsPlugin _plugin =
      FlutterLocalNotificationsPlugin();

  /// 通知被点击时的回调，由 app.dart 注入以跳转通知页。
  void Function(int notificationId)? onNotificationTap;

  bool _initialized = false;

  /// 已弹过的通知 id：FCM 前台转发与轮询可能拿到同一条审计（audit id 作 id），
  /// 用此集合保证只弹一次。
  final Set<int> _shownIds = <int>{};

  /// 登出/切换账号时清空已弹记录，避免新账号通知被旧记录吞掉。
  void resetShownIds() {
    _shownIds.clear();
  }

  /// flutter_test 环境无原生插件，初始化会抛 LateInitializationError，直接跳过。
  static bool get _isTestEnvironment =>
      Platform.environment['FLUTTER_TEST'] == 'true';

  Future<void> init({
    void Function(int notificationId)? onTap,
  }) async {
    onNotificationTap = onTap;
    if (_initialized || _isTestEnvironment) {
      return;
    }
    _initialized = true;

    const androidSettings = AndroidInitializationSettings('@mipmap/ic_launcher');
    final settings = InitializationSettings(
      android: androidSettings,
      windows: Platform.isWindows
          ? const WindowsInitializationSettings(
              appName: 'Synora',
              appUserModelId: 'com.example.synora',
              guid: '1f8c0d4e-9b2a-4a5d-8c6f-3e9d7a2b5c11',
            )
          : null,
    );
    await _plugin.initialize(
      settings,
      onDidReceiveNotificationResponse: (response) {
        final notificationId = response.id;
        if (notificationId != null) {
          onNotificationTap?.call(notificationId);
        }
      },
    );
  }

  Future<bool> requestNotificationsPermission() async {
    if (_isTestEnvironment) {
      return true;
    }
    final android = _plugin.resolvePlatformSpecificImplementation<
        AndroidFlutterLocalNotificationsPlugin>();
    if (android != null) {
      return await android.requestNotificationsPermission() ?? false;
    }
    // Windows 桌面无运行时权限弹窗。
    return true;
  }

  Future<void> show({
    required int id,
    required String title,
    required String body,
  }) async {
    if (_isTestEnvironment) {
      return;
    }
    if (!_shownIds.add(id)) {
      return; // 已弹过，跳过（轮询与 FCM 共用 audit id 幂等）。
    }
    if (!_initialized) {
      await init();
    }
    const androidDetails = AndroidNotificationDetails(
      'synora_system',
      'Synora 提醒',
      channelDescription: '日程提醒与主动跟进',
      importance: Importance.high,
      priority: Priority.high,
    );
    const details = NotificationDetails(android: androidDetails);
    await _plugin.show(id, title, body, details);
  }
}
