import 'package:flutter/material.dart';

import '../app_controller.dart';
import '../date_utils.dart';
import '../models.dart';

class NotificationsPage extends StatelessWidget {
  const NotificationsPage({super.key, required this.controller});

  final AppController controller;

  @override
  Widget build(BuildContext context) {
    return AnimatedBuilder(
      animation: controller,
      builder: (context, _) => Scaffold(
        appBar: AppBar(
          title: const Text('Notification history'),
          actions: <Widget>[
            IconButton(
              onPressed: controller.loadDashboard,
              icon: const Icon(Icons.refresh),
            ),
          ],
        ),
        body: ListView(
          padding: const EdgeInsets.all(16),
          children: <Widget>[
            if (controller.notifications.isEmpty)
              const _NotificationsEmpty()
            else
              ...controller.notifications.map(_NotificationTile.new),
          ],
        ),
      ),
    );
  }
}

class _NotificationTile extends StatelessWidget {
  const _NotificationTile(this.item);

  final NotificationItem item;

  Color _statusColor() {
    switch (item.status) {
      case 'delivered':
        return const Color(0xFF1D7A57);
      case 'failed':
        return const Color(0xFFB6523A);
      default:
        return const Color(0xFF746F55);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Card(
      margin: const EdgeInsets.only(bottom: 12),
      child: ListTile(
        title: Text(item.subject),
        subtitle: Text(
          '${item.channel} -> ${item.recipient}\nCreated: ${formatDateTime(item.createdAt)}${item.deliveredAt == null ? '' : '\nDelivered: ${formatDateTime(item.deliveredAt)}'}${item.errorMessage == null ? '' : '\nError: ${item.errorMessage}'}',
        ),
        trailing: Chip(
          label: Text(item.status),
          backgroundColor: _statusColor().withOpacity(0.12),
          labelStyle: TextStyle(color: _statusColor()),
        ),
        isThreeLine: true,
      ),
    );
  }
}

class _NotificationsEmpty extends StatelessWidget {
  const _NotificationsEmpty();

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(20),
      decoration: BoxDecoration(
        color: const Color(0xFFF5FAF8),
        borderRadius: BorderRadius.circular(20),
      ),
      child: const Text(
        'No reminder records yet. Create a schedule and the reminder history will appear here.',
      ),
    );
  }
}
