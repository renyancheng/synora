import 'package:flutter/material.dart';

import '../app_controller.dart';
import '../models.dart';
import '../strings.dart';
import 'quick_note_list_page.dart';

class QuickNoteTagCloudPage extends StatefulWidget {
  const QuickNoteTagCloudPage({super.key, required this.controller});

  final AppController controller;

  @override
  State<QuickNoteTagCloudPage> createState() => _QuickNoteTagCloudPageState();
}

class _QuickNoteTagCloudPageState extends State<QuickNoteTagCloudPage> {
  late Future<List<QuickNoteTagItem>> _future;

  @override
  void initState() {
    super.initState();
    _future = widget.controller.fetchQuickNoteTags();
  }

  Future<void> _openTag(String tag) async {
    await Navigator.of(context).push<void>(
      MaterialPageRoute<void>(
        builder: (_) => QuickNoteListPage(
          controller: widget.controller,
          initialTag: tag,
        ),
      ),
    );
    if (!mounted) {
      return;
    }
    setState(() {
      _future = widget.controller.fetchQuickNoteTags();
    });
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text(AppStrings.quickNoteTagsTitle)),
      body: FutureBuilder<List<QuickNoteTagItem>>(
        future: _future,
        builder: (context, snapshot) {
          if (snapshot.connectionState != ConnectionState.done) {
            return const Center(child: CircularProgressIndicator());
          }
          if (snapshot.hasError) {
            return Center(child: Text(snapshot.error.toString()));
          }
          final tags = snapshot.data ?? const <QuickNoteTagItem>[];
          if (tags.isEmpty) {
            return const Center(child: Text(AppStrings.emptyQuickNoteTags));
          }
          return SingleChildScrollView(
            padding: const EdgeInsets.all(16),
            child: Wrap(
              spacing: 10,
              runSpacing: 10,
              children: tags
                  .map(
                    (item) => ActionChip(
                      avatar: CircleAvatar(
                        radius: 12,
                        child: Text('${item.count}'),
                      ),
                      label: Text(item.tag),
                      onPressed: () => _openTag(item.tag),
                    ),
                  )
                  .toList(),
            ),
          );
        },
      ),
    );
  }
}
