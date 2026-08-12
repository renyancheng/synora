import 'package:firebase_core/firebase_core.dart';
import 'package:firebase_messaging/firebase_messaging.dart';
import 'package:flutter/foundation.dart';

import '../firebase_options.dart';
import 'system_notification_service.dart';

/// FCM 封装（仅 Android 平台生效）。
///
/// 通知链路：应用彻底关闭时 FCM notification message 由系统托盘直接展示（无需
/// Dart 代码）；运行中前台收到推送转 [SystemNotificationService.show] 弹本地通知
/// （保证前台可见），并按 `notification_audit_id` 与轮询路径幂等去重。Windows
/// 桌面不支持 FCM，仍由轮询 + flutter_local_notifications 承载。
///
/// 初始化失败（google-services 未配置 / 插件缺失）时静默降级为轮询通知，
/// 不阻塞主流程。
class FirebaseMessagingService {
  FirebaseMessagingService._();

  static final FirebaseMessagingService instance = FirebaseMessagingService._();

  /// token 初始/刷新回调，由 AppController 注入。需要登录态，未登录时缓存待登录后补注册。
  Future<void> Function(String token)? onTokenChanged;

  bool _initialized = false;

  bool get isSupported =>
      !kIsWeb && defaultTargetPlatform == TargetPlatform.android;

  Future<void> init() async {
    if (!isSupported || _initialized) {
      return;
    }
    try {
      await Firebase.initializeApp(
        options: DefaultFirebaseOptions.currentPlatform,
      );
      final messaging = FirebaseMessaging.instance;

      // Android 13+ 运行时通知权限（与本地通知共用）。
      await messaging.requestPermission(alert: true, badge: true, sound: true);

      final token = await messaging.getToken();
      if (token != null) {
        await _notifyTokenChanged(token);
      }
      messaging.onTokenRefresh.listen(_notifyTokenChanged);

      // 前台收到推送：转本地通知（通知 id 取审计 id，与轮询弹窗幂等）。
      FirebaseMessaging.onMessage.listen((RemoteMessage message) async {
        final notification = message.notification;
        if (notification == null) {
          return;
        }
        final auditId =
            int.tryParse(message.data['notification_audit_id'] ?? '');
        await SystemNotificationService.instance.show(
          id: auditId ?? message.messageId.hashCode,
          title: notification.title ?? 'Synora',
          body: notification.body ?? '',
        );
      });

      _initialized = true;
      debugPrint('FCM 初始化完成');
    } catch (error) {
      _initialized = false; // 允许下次重试
      debugPrint('FCM 初始化失败，降级为轮询通知：$error');
    }
  }

  Future<void> _notifyTokenChanged(String token) async {
    final handler = onTokenChanged;
    if (handler == null) {
      return;
    }
    try {
      await handler(token);
    } catch (error) {
      debugPrint('FCM token 上报失败：$error');
    }
  }
}
