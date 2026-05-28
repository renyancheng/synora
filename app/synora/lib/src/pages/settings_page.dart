import 'package:flutter/material.dart';

import '../app_controller.dart';
import '../strings.dart';
import 'memory_page.dart';
import 'notifications_page.dart';

class SettingsPage extends StatelessWidget {
  const SettingsPage({super.key, required this.controller});

  final AppController controller;

  Future<void> _openNotifications(BuildContext context) async {
    await Navigator.of(context).push<void>(
      MaterialPageRoute<void>(
        builder: (_) => NotificationsPage(controller: controller),
      ),
    );
  }

  Future<void> _openMemory(BuildContext context) async {
    await Navigator.of(context).push<void>(
      MaterialPageRoute<void>(
        builder: (_) => MemoryPage(controller: controller),
      ),
    );
  }

  Future<void> _confirmLogout(BuildContext context) async {
    final shouldLogout = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text(AppStrings.logoutConfirmTitle),
        content: const Text(AppStrings.logoutConfirmMessage),
        actions: <Widget>[
          TextButton(
            onPressed: () => Navigator.of(context).pop(false),
            child: const Text(AppStrings.cancel),
          ),
          FilledButton(
            onPressed: () => Navigator.of(context).pop(true),
            child: const Text(AppStrings.logout),
          ),
        ],
      ),
    );
    if (shouldLogout == true) {
      await controller.logout();
      if (context.mounted) {
        Navigator.of(context).popUntil((route) => route.isFirst);
      }
    }
  }

  Future<void> _editWebhook(BuildContext context) async {
    final textController = TextEditingController(
      text: controller.userPreferences.wecomRobotWebhook ?? '',
    );
    final result = await showDialog<String>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('企业微信机器人 Webhook'),
        content: TextField(
          controller: textController,
          minLines: 2,
          maxLines: 4,
          decoration: const InputDecoration(
            hintText: 'https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=...',
          ),
        ),
        actions: <Widget>[
          TextButton(
            onPressed: () => Navigator.of(context).pop(),
            child: const Text(AppStrings.cancel),
          ),
          FilledButton(
            onPressed: () => Navigator.of(context).pop(textController.text),
            child: const Text(AppStrings.saveChanges),
          ),
        ],
      ),
    );
    if (result == null) {
      return;
    }
    try {
      await controller.updateUserPreferences(
        wecomRobotWebhook: result.trim().isEmpty ? null : result.trim(),
      );
      if (context.mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text(AppStrings.saveSuccess)),
        );
      }
    } catch (error) {
      if (context.mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text(error.toString())),
        );
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final user = controller.session?.user;
    return Scaffold(
      appBar: AppBar(title: const Text(AppStrings.settings)),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: <Widget>[
          Card(
            child: Padding(
              padding: const EdgeInsets.all(18),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: <Widget>[
                  Text(AppStrings.userInfo, style: Theme.of(context).textTheme.titleMedium),
                  const SizedBox(height: 16),
                  Text(user?.displayName ?? AppStrings.noContent),
                  const SizedBox(height: 6),
                  Text(user?.email ?? AppStrings.noContent, style: Theme.of(context).textTheme.bodyMedium),
                ],
              ),
            ),
          ),
          const SizedBox(height: 12),
          Card(
            child: Column(
              children: <Widget>[
                ListTile(
                  leading: const Icon(Icons.notifications_outlined),
                  title: const Text(AppStrings.notificationHistory),
                  trailing: const Icon(Icons.chevron_right),
                  onTap: () => _openNotifications(context),
                ),
                const Divider(height: 1),
                ListTile(
                  leading: const Icon(Icons.psychology_alt_outlined),
                  title: const Text(AppStrings.memoryManagement),
                  trailing: const Icon(Icons.chevron_right),
                  onTap: () => _openMemory(context),
                ),
                const Divider(height: 1),
                ListTile(
                  leading: const Icon(Icons.link_outlined),
                  title: const Text('企业微信机器人 Webhook'),
                  subtitle: Text(
                    controller.userPreferences.wecomRobotWebhook?.trim().isNotEmpty == true
                        ? controller.userPreferences.wecomRobotWebhook!
                        : AppStrings.noContent,
                    maxLines: 2,
                    overflow: TextOverflow.ellipsis,
                  ),
                  onTap: () => _editWebhook(context),
                ),
                const Divider(height: 1),
                ListTile(
                  leading: const Icon(Icons.logout),
                  title: const Text(AppStrings.logout),
                  onTap: () => _confirmLogout(context),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}
