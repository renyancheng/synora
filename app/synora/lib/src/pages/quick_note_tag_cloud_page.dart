import 'package:flutter/material.dart';

import '../app_controller.dart';
import '../models.dart';
import '../strings.dart';
import '../tag_palette.dart';
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
          return LayoutBuilder(
            builder: (context, constraints) => SingleChildScrollView(
              padding: const EdgeInsets.all(20),
              child: ConstrainedBox(
                constraints: BoxConstraints(minHeight: constraints.maxHeight),
                child: Center(
                  child: Wrap(
                    alignment: WrapAlignment.center,
                    runAlignment: WrapAlignment.center,
                    spacing: 12,
                    runSpacing: 14,
                    children: tags.map((item) {
                      final colors = TagPalette.resolve(item.tag);
                      final fontSize = item.count >= 6 ? 18.0 : item.count >= 3 ? 15.0 : 13.0;
                      return InkWell(
                        borderRadius: BorderRadius.circular(999),
                        onTap: () => _openTag(item.tag),
                        child: Container(
                          padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
                          decoration: BoxDecoration(
                            color: colors.background,
                            borderRadius: BorderRadius.circular(999),
                            border: Border.all(color: colors.border),
                          ),
                          child: Text(
                            '${item.tag} · ${item.count}',
                            style: TextStyle(
                              color: colors.foreground,
                              fontSize: fontSize,
                              fontWeight: FontWeight.w600,
                            ),
                          ),
                        ),
                      );
                    }).toList(),
                  ),
                ),
              ),
            ),
          );
        },
      ),
    );
  }
}
