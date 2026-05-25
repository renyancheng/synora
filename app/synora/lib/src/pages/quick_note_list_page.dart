import 'package:flutter/material.dart';

import '../app_controller.dart';
import '../date_utils.dart';
import '../models.dart';
import '../strings.dart';

class QuickNoteListPage extends StatelessWidget {
  const QuickNoteListPage({super.key, required this.controller});

  final AppController controller;

  Future<void> _showDetails(BuildContext context, QuickNoteItem item) async {
    await showDialog<void>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text(AppStrings.quickNoteListTitle),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            Text(item.content),
            const SizedBox(height: 12),
            Text('${AppStrings.tagsField}：${item.tags.isEmpty ? AppStrings.noContent : item.tags.join('、')}'),
            const SizedBox(height: 8),
            Text('${AppStrings.selectedAttachments}：${item.sourceAttachmentIds.length}'),
            const SizedBox(height: 8),
            Text('${AppStrings.createTimeField}：${formatDateTime(item.createdAt)}'),
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

  Future<void> _showDeleteMenu(BuildContext context, QuickNoteItem item) async {
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
                title: const Text(AppStrings.deleteQuickNoteTitle),
                content: const Text(AppStrings.deleteQuickNoteMessage),
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
              await controller.deleteQuickNoteItem(item.id);
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
        appBar: AppBar(title: const Text(AppStrings.quickNoteListTitle)),
        body: ListView(
          padding: const EdgeInsets.all(16),
          children: <Widget>[
            if (controller.quickNotes.isEmpty)
              const _EmptyPanel(AppStrings.emptyQuickNotes)
            else
              ...controller.quickNotes.map(
                (item) => Card(
                  margin: const EdgeInsets.only(bottom: 12),
                  child: ListTile(
                    title: Text(item.content, maxLines: 2, overflow: TextOverflow.ellipsis),
                    subtitle: Text(
                      '${item.tags.isEmpty ? AppStrings.noContent : item.tags.join(' / ')}\n${formatDateTime(item.createdAt)}',
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
