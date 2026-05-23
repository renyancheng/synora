import 'package:flutter/material.dart';

import '../app_controller.dart';
import '../date_utils.dart';
import '../models.dart';
import '../strings.dart';


class ScheduleListPage extends StatelessWidget {
  const ScheduleListPage({super.key, required this.controller});

  final AppController controller;

  Future<void> _showDetails(BuildContext context, ScheduleItem item) async {
    await showDialog<void>(
      context: context,
      builder: (context) => AlertDialog(
        title: Text(item.title),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            Text('${AppStrings.startField}：${formatEventRange(start: item.start, end: item.end, isAllDay: item.isAllDay)}'),
            const SizedBox(height: 8),
            Text('${AppStrings.reminderField}：${formatReminderOffsets(item.reminderOffsetsMinutes)}'),
            const SizedBox(height: 8),
            Text('${AppStrings.recurrenceField}：${formatRecurrence(item.recurrence)}'),
            if (item.location != null) ...<Widget>[
              const SizedBox(height: 8),
              Text('${AppStrings.locationField}：${item.location}'),
            ],
            const SizedBox(height: 8),
            Text('${AppStrings.detailsField}：${item.details}'),
            const SizedBox(height: 8),
            Text('${AppStrings.timeZoneField}：${item.start.timeZone}'),
            const SizedBox(height: 8),
            Text('${AppStrings.parseConfidenceField}：${(item.parseConfidence * 100).toStringAsFixed(0)}%'),
          ],
        ),
        actions: <Widget>[
          TextButton(
            onPressed: () => Navigator.of(context).pop(),
            child: const Text(AppStrings.confirmAction),
          ),
        ],
      ),
    );
  }

  Future<void> _showDeleteMenu(BuildContext context, ScheduleItem item) async {
    await showModalBottomSheet<void>(
      context: context,
      builder: (sheetContext) => SafeArea(
        child: ListTile(
          leading: const Icon(Icons.delete_outline),
          title: const Text(AppStrings.delete),
          onTap: () async {
            Navigator.of(sheetContext).pop();
            final confirmed = await showDialog<bool>(
              context: context,
              builder: (context) => AlertDialog(
                title: const Text(AppStrings.deleteScheduleTitle),
                content: const Text(AppStrings.deleteScheduleMessage),
                actions: <Widget>[
                  TextButton(
                    onPressed: () => Navigator.of(context).pop(false),
                    child: const Text(AppStrings.cancel),
                  ),
                  FilledButton(
                    onPressed: () => Navigator.of(context).pop(true),
                    child: const Text(AppStrings.delete),
                  ),
                ],
              ),
            );
            if (confirmed == true) {
              await controller.deleteScheduleItem(item.id);
              if (context.mounted) {
                ScaffoldMessenger.of(context).showSnackBar(
                  const SnackBar(content: Text(AppStrings.deleteDone)),
                );
              }
            }
          },
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    return AnimatedBuilder(
      animation: controller,
      builder: (context, _) => Scaffold(
        appBar: AppBar(title: const Text(AppStrings.scheduleListTitle)),
        body: ListView(
          padding: const EdgeInsets.all(16),
          children: <Widget>[
            if (controller.schedules.isEmpty)
              const _EmptyPanel(AppStrings.emptySchedules)
            else
              ...controller.schedules.map(
                (item) => Card(
                  margin: const EdgeInsets.only(bottom: 12),
                  child: ListTile(
                    title: Text(item.title),
                    subtitle: Text(
                      '${formatEventRange(start: item.start, end: item.end, isAllDay: item.isAllDay)}'
                      '${item.location == null ? '' : '\n${item.location}'}\n'
                      '${formatRecurrence(item.recurrence)}',
                    ),
                    isThreeLine: true,
                    onTap: () => _showDetails(context, item),
                    onLongPress: () => _showDeleteMenu(context, item),
                  ),
                ),
              ),
          ],
        ),
      ),
    );
  }
}


class _EmptyPanel extends StatelessWidget {
  const _EmptyPanel(this.text);

  final String text;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(20),
      decoration: BoxDecoration(
        color: const Color(0xFFF5FAF8),
        borderRadius: BorderRadius.circular(20),
      ),
      child: Text(text),
    );
  }
}
