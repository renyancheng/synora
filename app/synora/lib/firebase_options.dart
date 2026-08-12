// 手动从 android/app/google-services.json 转换的 Firebase 配置。
// 仅 Android 平台：Windows 桌面通知由轮询 + flutter_local_notifications 承载，
// FCM 仅用于安卓应用彻底关闭时的推送补充。
import 'package:firebase_core/firebase_core.dart' show FirebaseOptions;
import 'package:flutter/foundation.dart'
    show defaultTargetPlatform, kIsWeb, TargetPlatform;

class DefaultFirebaseOptions {
  static FirebaseOptions get currentPlatform {
    if (kIsWeb) {
      throw UnsupportedError(
        'DefaultFirebaseOptions 未配置 Web 平台。',
      );
    }
    switch (defaultTargetPlatform) {
      case TargetPlatform.android:
        return android;
      case TargetPlatform.iOS:
      case TargetPlatform.macOS:
      case TargetPlatform.windows:
      case TargetPlatform.linux:
      case TargetPlatform.fuchsia:
        throw UnsupportedError(
          'DefaultFirebaseOptions 仅为 Android 平台生成，当前平台 '
          '${defaultTargetPlatform.name} 不受支持。',
        );
    }
  }

  static const FirebaseOptions android = FirebaseOptions(
    apiKey: 'AIzaSyDStL-TQJ-XZSOWuMcHy__R1svj-uYSyow',
    appId: '1:110091900284:android:ec202dc9340be4036880f9',
    messagingSenderId: '110091900284',
    projectId: 'synora-18495',
    storageBucket: 'synora-18495.firebasestorage.app',
  );
}
