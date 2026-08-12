import 'package:flutter_test/flutter_test.dart';
import 'package:synora/src/strings.dart';

void main() {
  test('系统通知 channel 显示为中文', () {
    expect(AppStrings.channelLabel('system'), '系统通知');
  });

  test('通知失败原因为空时不显示原因', () {
    expect(AppStrings.notificationFailureReason('system', null), isNull);
    expect(AppStrings.notificationFailureReason('system', '  '), isNull);
  });

  test('过长的失败原因被截断', () {
    final longMessage = '错误' * 80;
    final reason = AppStrings.notificationFailureReason('system', longMessage);
    expect(reason, isNotNull);
    expect(reason!.length, lessThanOrEqualTo(121));
    expect(reason, endsWith('…'));
  });

  test('短失败原因原样透传', () {
    expect(
      AppStrings.notificationFailureReason('system', 'FCM 未配置'),
      'FCM 未配置',
    );
  });
}
