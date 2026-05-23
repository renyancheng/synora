import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';

import 'app_controller.dart';
import 'app_scope.dart';
import 'pages/chat_home_page.dart';
import 'pages/login_page.dart';
import 'strings.dart';

class SynoraApp extends StatefulWidget {
  const SynoraApp({super.key, this.controller});

  final AppController? controller;

  @override
  State<SynoraApp> createState() => _SynoraAppState();
}

class _SynoraAppState extends State<SynoraApp> {
  late final AppController _controller;

  @override
  void initState() {
    super.initState();
    _controller = widget.controller ?? AppController();
  }

  @override
  void dispose() {
    if (widget.controller == null) {
      _controller.dispose();
    }
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
            home: _controller.isAuthenticated
                ? ChatHomePage(controller: _controller)
                : LoginPage(controller: _controller),
          );
        },
      ),
    );
  }
}
