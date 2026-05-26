import 'package:flutter/material.dart';

import '../app_controller.dart';
import '../date_utils.dart';
import '../strings.dart';
import 'quick_note_detail_page.dart';

class QuickNoteListPage extends StatelessWidget {
  const QuickNoteListPage({super.key, required this.controller});

  final AppController controller;

  Future<void> _openDetails(BuildContext context, int index) async {
    final item = controller.quickNotes[index];
    final changed = await Navigator.of(context).push<bool>(
      MaterialPageRoute<bool>(
        builder: (_) => QuickNoteDetailPage(controller: controller, item: item),
      ),
    );
    if (changed == true && context.mounted) {
      // AnimatedBuilder will handle refresh.
    }
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
              ...controller.quickNotes.asMap().entries.map(
                (entry) => Card(
                  margin: const EdgeInsets.only(bottom: 12),
                  child: ListTile(
                    title: Text(entry.value.content, maxLines: 2, overflow: TextOverflow.ellipsis),
                    subtitle: Text(
                      '${entry.value.tags.isEmpty ? AppStrings.noContent : entry.value.tags.join(' / ')}\n${formatDateTime(entry.value.createdAt)}',
                    ),
                    isThreeLine: true,
                    onTap: () => _openDetails(context, entry.key),
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
