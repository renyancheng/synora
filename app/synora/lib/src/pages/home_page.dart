import 'package:flutter/material.dart';

import '../app_controller.dart';
import '../date_utils.dart';
import '../models.dart';
import 'notifications_page.dart';
import 'quick_note_page.dart';
import 'schedule_draft_page.dart';

class HomePage extends StatelessWidget {
  const HomePage({super.key, required this.controller});

  final AppController controller;

  Future<void> _openScheduleComposer(BuildContext context) async {
    await Navigator.of(context).push<bool>(
      MaterialPageRoute<bool>(
        builder: (_) => ScheduleDraftPage(controller: controller),
      ),
    );
  }

  Future<void> _openQuickNotes(BuildContext context) async {
    await Navigator.of(context).push<void>(
      MaterialPageRoute<void>(
        builder: (_) => QuickNotePage(controller: controller),
      ),
    );
  }

  Future<void> _openNotifications(BuildContext context) async {
    await Navigator.of(context).push<void>(
      MaterialPageRoute<void>(
        builder: (_) => NotificationsPage(controller: controller),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final user = controller.session?.user;
    return Scaffold(
      appBar: AppBar(
        title: const Text('Synora Workspace'),
        actions: <Widget>[
          IconButton(
            tooltip: 'Refresh',
            onPressed: controller.isLoading ? null : controller.loadDashboard,
            icon: const Icon(Icons.refresh),
          ),
          IconButton(
            tooltip: 'Sign out',
            onPressed: controller.logout,
            icon: const Icon(Icons.logout),
          ),
        ],
      ),
      body: RefreshIndicator(
        onRefresh: controller.loadDashboard,
        child: ListView(
          padding: const EdgeInsets.all(16),
          children: <Widget>[
            Container(
              padding: const EdgeInsets.all(20),
              decoration: BoxDecoration(
                borderRadius: BorderRadius.circular(24),
                gradient: const LinearGradient(
                  colors: <Color>[Color(0xFF1B6F5F), Color(0xFF2B8A78)],
                  begin: Alignment.topLeft,
                  end: Alignment.bottomRight,
                ),
              ),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: <Widget>[
                  Text(
                    'Welcome back, ${user?.displayName ?? 'Teacher Han'}',
                    style: Theme.of(context)
                        .textTheme
                        .headlineSmall
                        ?.copyWith(color: Colors.white),
                  ),
                  const SizedBox(height: 8),
                  const Text(
                    'Handle important reminders first, then capture ideas quickly. This MVP focuses on text flows plus explicit approval.',
                    style: TextStyle(color: Colors.white70, height: 1.5),
                  ),
                ],
              ),
            ),
            const SizedBox(height: 16),
            Row(
              children: <Widget>[
                Expanded(
                  child: _ActionCard(
                    title: 'New schedule',
                    subtitle: 'Text parsing, conflict checks, approval save',
                    icon: Icons.event_note,
                    onTap: () => _openScheduleComposer(context),
                  ),
                ),
                const SizedBox(width: 12),
                Expanded(
                  child: _ActionCard(
                    title: 'Quick note',
                    subtitle: 'Preview tags first, then approve save',
                    icon: Icons.sticky_note_2_outlined,
                    onTap: () => _openQuickNotes(context),
                  ),
                ),
              ],
            ),
            const SizedBox(height: 12),
            _ActionCard(
              title: 'Notification history',
              subtitle: 'Check email and mock WeCom delivery records',
              icon: Icons.mark_email_read_outlined,
              onTap: () => _openNotifications(context),
            ),
            const SizedBox(height: 24),
            Text(
              'Upcoming schedules',
              style: Theme.of(context).textTheme.titleLarge,
            ),
            const SizedBox(height: 12),
            if (controller.schedules.isEmpty)
              const _EmptyState(
                title: 'No schedules yet',
                subtitle:
                    'Start with "New schedule" and paste a short natural-language note.',
              )
            else
              ...controller.schedules.map(_ScheduleTile.new),
          ],
        ),
      ),
    );
  }
}

class _ActionCard extends StatelessWidget {
  const _ActionCard({
    required this.title,
    required this.subtitle,
    required this.icon,
    required this.onTap,
  });

  final String title;
  final String subtitle;
  final IconData icon;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return Card(
      elevation: 1,
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(20)),
      child: InkWell(
        onTap: onTap,
        borderRadius: BorderRadius.circular(20),
        child: Padding(
          padding: const EdgeInsets.all(18),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: <Widget>[
              Icon(icon, color: const Color(0xFF1B6F5F)),
              const SizedBox(height: 12),
              Text(title, style: Theme.of(context).textTheme.titleMedium),
              const SizedBox(height: 8),
              Text(subtitle, style: Theme.of(context).textTheme.bodyMedium),
            ],
          ),
        ),
      ),
    );
  }
}

class _ScheduleTile extends StatelessWidget {
  const _ScheduleTile(this.item);

  final ScheduleItem item;

  @override
  Widget build(BuildContext context) {
    return Card(
      margin: const EdgeInsets.only(bottom: 12),
      child: ListTile(
        leading: const CircleAvatar(
          backgroundColor: Color(0xFFDAF0EA),
          child: Icon(Icons.schedule, color: Color(0xFF1B6F5F)),
        ),
        title: Text(item.title),
        subtitle: Text(
          '${formatDateTime(item.scheduledAt)}\nReminder: ${formatDateTime(item.reminderAt)}${item.location == null ? '' : '\nLocation: ${item.location}'}',
        ),
        isThreeLine: item.location != null,
      ),
    );
  }
}

class _EmptyState extends StatelessWidget {
  const _EmptyState({required this.title, required this.subtitle});

  final String title;
  final String subtitle;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(20),
      decoration: BoxDecoration(
        color: const Color(0xFFF5FAF8),
        borderRadius: BorderRadius.circular(20),
      ),
      child: Column(
        children: <Widget>[
          const Icon(
            Icons.inbox_outlined,
            size: 36,
            color: Color(0xFF5B8178),
          ),
          const SizedBox(height: 12),
          Text(title, style: Theme.of(context).textTheme.titleMedium),
          const SizedBox(height: 6),
          Text(subtitle, textAlign: TextAlign.center),
        ],
      ),
    );
  }
}
