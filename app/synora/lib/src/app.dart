import 'package:flutter/material.dart';

import 'app_controller.dart';
import 'app_scope.dart';
import 'pages/home_page.dart';
import 'pages/login_page.dart';

class SynoraApp extends StatefulWidget {
  const SynoraApp({super.key});

  @override
  State<SynoraApp> createState() => _SynoraAppState();
}

class _SynoraAppState extends State<SynoraApp> {
  late final AppController _controller;

  @override
  void initState() {
    super.initState();
    _controller = AppController();
  }

  @override
  void dispose() {
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
            title: 'Synora',
            theme: ThemeData(
              colorScheme: colorScheme,
              useMaterial3: true,
              scaffoldBackgroundColor: const Color(0xFFF8FBFA),
              inputDecorationTheme: InputDecorationTheme(
                border: OutlineInputBorder(borderRadius: BorderRadius.circular(16)),
              ),
              cardTheme: CardTheme(
                shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(20)),
              ),
            ),
            home: _controller.isAuthenticated ? HomePage(controller: _controller) : LoginPage(controller: _controller),
          );
        },
      ),
    );
  }
}
