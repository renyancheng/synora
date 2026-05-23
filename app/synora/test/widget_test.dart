import 'package:flutter_test/flutter_test.dart';

import 'package:synora/src/app.dart';

void main() {
  testWidgets('shows login screen on cold start', (tester) async {
    await tester.pumpWidget(const SynoraApp());

    expect(find.text('Synora'), findsOneWidget);
    expect(find.text('Open workspace'), findsOneWidget);
    expect(find.text('Email'), findsOneWidget);
  });
}
