import 'package:flutter/material.dart';

import '../app_controller.dart';
import '../date_utils.dart';
import '../models.dart';
import '../strings.dart';
import 'quick_note_detail_page.dart';
import 'quick_note_tag_cloud_page.dart';

class QuickNoteListPage extends StatefulWidget {
  const QuickNoteListPage({
    super.key,
    required this.controller,
    this.initialTag,
  });

  final AppController controller;
  final String? initialTag;

  @override
  State<QuickNoteListPage> createState() => _QuickNoteListPageState();
}

class _QuickNoteListPageState extends State<QuickNoteListPage> {
  late Future<List<QuickNoteItem>> _future;
  late String? _activeTag;

  @override
  void initState() {
    super.initState();
    _activeTag = widget.initialTag?.trim().isEmpty == true
        ? null
        : widget.initialTag?.trim();
    _future = widget.controller.fetchQuickNotesByTag(_activeTag);
  }

  Future<void> _reload() async {
    setState(() {
      _future = widget.controller.fetchQuickNotesByTag(_activeTag);
    });
  }

  Future<void> _clearFilter() async {
    setState(() {
      _activeTag = null;
      _future = widget.controller.fetchQuickNotesByTag(null);
    });
  }

  Future<void> _openTagCloud() async {
    await Navigator.of(context).push<void>(
      MaterialPageRoute<void>(
        builder: (_) => QuickNoteTagCloudPage(controller: widget.controller),
      ),
    );
    if (!mounted) {
      return;
    }
    await _reload();
  }

  Future<void> _openDetails(QuickNoteItem item) async {
    final changed = await Navigator.of(context).push<bool>(
      MaterialPageRoute<bool>(
        builder: (_) => QuickNoteDetailPage(controller: widget.controller, item: item),
      ),
    );
    if (changed == true && mounted) {
      await _reload();
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text(AppStrings.quickNoteListTitle),
        actions: <Widget>[
          IconButton(
            onPressed: _openTagCloud,
            icon: const Icon(Icons.sell_outlined),
            tooltip: AppStrings.quickNoteTagsTitle,
          ),
        ],
      ),
      body: FutureBuilder<List<QuickNoteItem>>(
        future: _future,
        builder: (context, snapshot) {
          if (snapshot.connectionState != ConnectionState.done) {
            return const Center(child: CircularProgressIndicator());
          }
          if (snapshot.hasError) {
            return Center(child: Text(snapshot.error.toString()));
          }
          final items = snapshot.data ?? const <QuickNoteItem>[];
          return ListView(
            padding: const EdgeInsets.all(16),
            children: <Widget>[
              if (_activeTag != null)
                Padding(
                  padding: const EdgeInsets.only(bottom: 12),
                  child: Wrap(
                    spacing: 8,
                    runSpacing: 8,
                    children: <Widget>[
                      Chip(
                        label: Text('${AppStrings.filterByTag}：$_activeTag'),
                      ),
                      ActionChip(
                        label: const Text(AppStrings.clearTagFilter),
                        onPressed: _clearFilter,
                      ),
                    ],
                  ),
                ),
              if (items.isEmpty)
                const _EmptyPanel(AppStrings.emptyQuickNotes)
              else
                ...items.map(
                  (item) => Card(
                    margin: const EdgeInsets.only(bottom: 12),
                    child: ListTile(
                      title: Text(
                        item.content,
                        maxLines: 2,
                        overflow: TextOverflow.ellipsis,
                      ),
                      subtitle: Text(
                        '${item.tags.isEmpty ? AppStrings.noContent : item.tags.join(' / ')}\n${formatDateTime(item.createdAt)}',
                      ),
                      isThreeLine: true,
                      onTap: () => _openDetails(item),
                    ),
                  ),
                ),
            ],
          );
        },
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
