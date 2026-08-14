import 'package:flutter/material.dart';

import '../app_controller.dart';
import '../strings.dart';

class LoginPage extends StatefulWidget {
  const LoginPage({super.key, required this.controller});

  final AppController controller;

  @override
  State<LoginPage> createState() => _LoginPageState();
}

class _LoginPageState extends State<LoginPage> {
  final _formKey = GlobalKey<FormState>();
  late final TextEditingController _emailController;
  late final TextEditingController _passwordController;
  late final TextEditingController _displayNameController;
  bool _registerMode = false;

  /// 演示凭据只在显式启用 demo 配置时预填：
  /// 构建时传入 --dart-define=SYNORA_DEMO_CREDENTIALS=true。
  /// 生产构建不携带该 define，输入框保持为空。
  static const bool _demoCredentialsEnabled = bool.fromEnvironment(
    'SYNORA_DEMO_CREDENTIALS',
  );

  @override
  void initState() {
    super.initState();
    final lastUser = widget.controller.lastKnownUser;
    _emailController = TextEditingController(
      text: lastUser?.email ?? (_demoCredentialsEnabled ? 'han.teacher@example.com' : ''),
    );
    _passwordController = TextEditingController(
      text: lastUser == null && _demoCredentialsEnabled ? 'SynoraMVP123!' : '',
    );
    _displayNameController = TextEditingController(
      text: lastUser?.displayName ?? '',
    );
  }

  @override
  void dispose() {
    _emailController.dispose();
    _passwordController.dispose();
    _displayNameController.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    if (!_formKey.currentState!.validate()) {
      return;
    }
    try {
      if (_registerMode) {
        await widget.controller.register(
          email: _emailController.text.trim(),
          password: _passwordController.text,
          displayName: _displayNameController.text.trim(),
        );
      } else {
        await widget.controller.login(
          _emailController.text.trim(),
          _passwordController.text,
        );
      }
    } catch (error) {
      if (!mounted) {
        return;
      }
      ScaffoldMessenger.of(
        context,
      ).showSnackBar(SnackBar(content: Text(error.toString())));
    }
  }

  void _toggleMode() {
    setState(() {
      _registerMode = !_registerMode;
    });
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final lastUser = widget.controller.lastKnownUser;
    final busyText = _registerMode
        ? AppStrings.registering
        : AppStrings.loggingIn;
    final submitText = _registerMode
        ? AppStrings.registerButton
        : AppStrings.loginButton;
    return Scaffold(
      // SafeArea + 可滚动布局：软键盘弹起、小屏、大字体下所有字段与按钮均可达。
      body: Container(
        decoration: const BoxDecoration(
          gradient: LinearGradient(
            colors: <Color>[Color(0xFFF2FBF7), Color(0xFFD8EFE7)],
            begin: Alignment.topLeft,
            end: Alignment.bottomRight,
          ),
        ),
        child: SafeArea(
          child: LayoutBuilder(
            builder: (context, constraints) {
              return SingleChildScrollView(
                padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 24),
                child: ConstrainedBox(
                  constraints: BoxConstraints(
                    minHeight: constraints.maxHeight - 48,
                  ),
                  child: Center(
                    child: ConstrainedBox(
                      constraints: const BoxConstraints(maxWidth: 420),
                      child: Card(
                        margin: EdgeInsets.zero,
                        elevation: 2,
                        child: Padding(
                          padding: const EdgeInsets.all(24),
                          child: Form(
                            key: _formKey,
                            child: Column(
                              mainAxisSize: MainAxisSize.min,
                              crossAxisAlignment: CrossAxisAlignment.start,
                              children: <Widget>[
                                Text(
                                  AppStrings.appTitle,
                                  style: theme.textTheme.headlineMedium?.copyWith(
                                    fontWeight: FontWeight.w700,
                                    color: const Color(0xFF11483E),
                                  ),
                                ),
                                const SizedBox(height: 10),
                                Text(
                                  _registerMode
                                      ? AppStrings.registerTitle
                                      : AppStrings.loginSubtitle,
                                  style: theme.textTheme.bodyMedium?.copyWith(
                                    color: const Color(0xFF275C52),
                                    height: 1.5,
                                  ),
                                ),
                                if (lastUser != null) ...<Widget>[
                                  const SizedBox(height: 14),
                                  Text(
                                    '${AppStrings.lastSignedInAs}：${lastUser.displayName}（${lastUser.email}）',
                                    style: theme.textTheme.bodySmall?.copyWith(
                                      color: const Color(0xFF486B63),
                                    ),
                                  ),
                                ],
                                const SizedBox(height: 24),
                                if (_registerMode) ...<Widget>[
                                  TextFormField(
                                    controller: _displayNameController,
                                    decoration: const InputDecoration(
                                      labelText: AppStrings.displayNameLabel,
                                    ),
                                    validator: (value) =>
                                        (value == null ||
                                            value.trim().isEmpty)
                                        ? AppStrings.displayNameRequired
                                        : null,
                                  ),
                                  const SizedBox(height: 16),
                                ],
                                TextFormField(
                                  controller: _emailController,
                                  keyboardType: TextInputType.emailAddress,
                                  decoration: const InputDecoration(
                                    labelText: AppStrings.emailLabel,
                                  ),
                                  validator: (value) =>
                                      (value == null || value.trim().isEmpty)
                                      ? AppStrings.emailRequired
                                      : null,
                                ),
                                const SizedBox(height: 16),
                                TextFormField(
                                  controller: _passwordController,
                                  decoration: const InputDecoration(
                                    labelText: AppStrings.passwordLabel,
                                  ),
                                  obscureText: true,
                                  validator: (value) =>
                                      (value == null || value.isEmpty)
                                      ? AppStrings.passwordRequired
                                      : null,
                                ),
                                const SizedBox(height: 24),
                                SizedBox(
                                  width: double.infinity,
                                  child: FilledButton(
                                    onPressed: widget.controller.isLoading
                                        ? null
                                        : _submit,
                                    child: Text(
                                      widget.controller.isLoading
                                          ? busyText
                                          : submitText,
                                    ),
                                  ),
                                ),
                                const SizedBox(height: 12),
                                Align(
                                  alignment: Alignment.centerRight,
                                  child: TextButton(
                                    onPressed: widget.controller.isLoading
                                        ? null
                                        : _toggleMode,
                                    child: Text(
                                      _registerMode
                                          ? AppStrings.switchToLogin
                                          : AppStrings.switchToRegister,
                                    ),
                                  ),
                                ),
                              ],
                            ),
                          ),
                        ),
                      ),
                    ),
                  ),
                ),
              );
            },
          ),
        ),
      ),
    );
  }
}
