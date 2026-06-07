import 'dart:async';

import 'package:flutter/material.dart';

import '../app_controller.dart';
import '../date_utils.dart';
import '../models.dart';
import '../strings.dart';
import '../tag_palette.dart';
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
  final TextEditingController _searchController = TextEditingController();
  Timer? _searchDebounce;
  String _searchQuery = '';

  @override
  void initState() {
    super.initState();
    _activeTag = widget.initialTag?.trim().isEmpty == true
        ? null
        : widget.initialTag?.trim();
    _future = widget.controller.fetchQuickNotesByTag(
      _activeTag,
      query: _searchQuery,
    );
  }

  Future<void> _reload() async {
    setState(() {
      _future = widget.controller.fetchQuickNotesByTag(
        _activeTag,
        query: _searchQuery,
      );
    });
  }

  Future<void> _clearFilter() async {
    setState(() {
      _activeTag = null;
      _future = widget.controller.fetchQuickNotesByTag(
        null,
        query: _searchQuery,
      );
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
        builder: (_) =>
            QuickNoteDetailPage(controller: widget.controller, item: item),
      ),
    );
    if (changed == true && mounted) {
      await _reload();
    }
  }

  @override
  void dispose() {
    _searchDebounce?.cancel();
    _searchController.dispose();
    super.dispose();
  }

  void _handleSearchChanged(String value) {
    _searchDebounce?.cancel();
    _searchDebounce = Timer(const Duration(milliseconds: 300), () {
      if (!mounted) {
        return;
      }
      setState(() {
        _searchQuery = value.trim();
        _future = widget.controller.fetchQuickNotesByTag(
          _activeTag,
          query: _searchQuery,
        );
      });
    });
  }

  String _noteTitle(String content) {
    final lines = content
        .split('\n')
        .map((line) => line.trim())
        .where((line) => line.isNotEmpty);
    final firstLine = lines.isNotEmpty ? lines.first : content.trim();
    final normalized = firstLine.replaceAll(RegExp(r'\s+'), ' ').trim();
    return normalized.isEmpty ? AppStrings.noContent : normalized;
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
              Padding(
                padding: const EdgeInsets.only(bottom: 12),
                child: TextField(
                  controller: _searchController,
                  onChanged: _handleSearchChanged,
                  decoration: InputDecoration(
                    hintText: AppStrings.searchQuickNotesHint,
                    prefixIcon: const Icon(Icons.search),
                    suffixIcon: _searchQuery.isEmpty
                        ? null
                        : IconButton(
                            onPressed: () {
                              _searchController.clear();
                              _handleSearchChanged('');
                            },
                            icon: const Icon(Icons.close),
                          ),
                    border: OutlineInputBorder(
                      borderRadius: BorderRadius.circular(16),
                    ),
                  ),
                ),
              ),
              if (_activeTag != null)
                Padding(
                  padding: const EdgeInsets.only(bottom: 12),
                  child: Wrap(
                    spacing: 8,
                    runSpacing: 8,
                    children: <Widget>[
                      Chip(
                        label: Text('${AppStrings.filterByTag}：$_activeTag'),
                        backgroundColor: TagPalette.resolve(
                          _activeTag!,
                        ).background,
                        side: BorderSide(
                          color: TagPalette.resolve(_activeTag!).border,
                        ),
                        labelStyle: TextStyle(
                          color: TagPalette.resolve(_activeTag!).foreground,
                        ),
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
                    child: InkWell(
                      borderRadius: BorderRadius.circular(12),
                      onTap: () => _openDetails(item),
                      child: Padding(
                        padding: const EdgeInsets.all(16),
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: <Widget>[
                            Text(
                              _noteTitle(item.content),
                              maxLines: 1,
                              overflow: TextOverflow.ellipsis,
                              style: Theme.of(context).textTheme.titleMedium,
                            ),
                            if (item.tags.isNotEmpty) ...<Widget>[
                              const SizedBox(height: 10),
                              Wrap(
                                spacing: 6,
                                runSpacing: 6,
                                children: item.tags.map((tag) {
                                  final colors = TagPalette.resolve(tag);
                                  return Container(
                                    padding: const EdgeInsets.symmetric(
                                      horizontal: 8,
                                      vertical: 4,
                                    ),
                                    decoration: BoxDecoration(
                                      color: colors.background,
                                      borderRadius: BorderRadius.circular(999),
                                      border: Border.all(color: colors.border),
                                    ),
                                    child: Text(
                                      tag,
                                      style: TextStyle(
                                        color: colors.foreground,
                                        fontSize: 11,
                                        fontWeight: FontWeight.w600,
                                      ),
                                    ),
                                  );
                                }).toList(),
                              ),
                            ],
                            const SizedBox(height: 10),
                            Text(
                              formatDateTime(item.createdAt),
                              style: Theme.of(context).textTheme.bodySmall
                                  ?.copyWith(
                                    color: Theme.of(
                                      context,
                                    ).colorScheme.onSurfaceVariant,
                                  ),
                            ),
                          ],
                        ),
                      ),
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
