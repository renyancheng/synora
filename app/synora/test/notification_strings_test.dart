import 'package:flutter_test/flutter_test.dart';
import 'package:synora/src/strings.dart';


void main() {
  test('企业微信错误原因映射为中文', () {
    expect(
      AppStrings.notificationFailureReason('wecom_robot', '企业微信群机器人返回错误码 93000: invalid webhook url'),
      '企业微信机器人拒绝了本次消息（错误码 93000）。',
    );
    expect(
      AppStrings.notificationFailureReason('wecom_robot', 'timed out'),
      '企业微信推送超时，请稍后重试。',
    );
  });

  test('邮件错误原因映射为中文', () {
    expect(
      AppStrings.notificationFailureReason('email', 'Connection refused'),
      '邮件服务连接失败，请检查 SMTP 配置。',
    );
  });
}
