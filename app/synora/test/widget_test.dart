import 'package:flutter_test/flutter_test.dart';

import 'package:synora/src/app.dart';

void main() {
  testWidgets('冷启动展示中文登录页', (tester) async {
    await tester.pumpWidget(const SynoraApp());

    expect(find.text('Synora 生活备忘助手'), findsOneWidget);
    expect(find.text('登录'), findsOneWidget);
    expect(find.text('邮箱'), findsOneWidget);
  });
}
