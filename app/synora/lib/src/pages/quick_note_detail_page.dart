import 'package:flutter/material.dart';
import 'package:flutter_markdown/flutter_markdown.dart';

import '../app_controller.dart';
import '../date_utils.dart';
import '../models.dart';
import '../strings.dart';
import 'tag_input_field.dart';

class QuickNoteDetailPage extends StatefulWidget {
  const QuickNoteDetailPage({
    super.key,
    required this.controller,
    required this.item,
  });

  final AppController controller;
  final QuickNoteItem item;

  @override
  State<QuickNoteDetailPage> createState() => _QuickNoteDetailPageState();
}

class _QuickNoteDetailPageState extends State<QuickNoteDetailPage> {
  late QuickNoteItem _item;
  late TextEditingController _contentController;
  late List<String> _tags;
  bool _editing = false;
  bool _saving = false;

  @override
  void initState() {
    super.initState();
    _item = widget.item;
    _contentController = TextEditingController(text: _item.content);
    _tags = List<String>.from(_item.tags);
  }

  @override
  void dispose() {
    _contentController.dispose();
    super.dispose();
  }

  Future<void> _delete() async {
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
    if (confirmed != true) {
      return;
    }
    await widget.controller.deleteQuickNoteItem(_item.id);
    if (!mounted) {
      return;
    }
    ScaffoldMessenger.of(
      context,
    ).showSnackBar(const SnackBar(content: Text(AppStrings.deleteDone)));
    Navigator.of(context).pop(true);
  }

  Future<void> _save() async {
    final content = _contentController.text.trim();
    if (content.isEmpty) {
      _showMessage(AppStrings.quickNoteContentRequired);
      return;
    }
    setState(() => _saving = true);
    try {
      final updated = await widget.controller.updateQuickNote(
        noteId: _item.id,
        content: content,
        tags: _tags,
      );
      if (!mounted) {
        return;
      }
      setState(() {
        _item = updated;
        _editing = false;
        _contentController.text = updated.content;
        _tags = List<String>.from(updated.tags);
      });
      _showMessage(AppStrings.saveSuccess);
    } catch (error) {
      _showMessage(error.toString());
    } finally {
      if (mounted) {
        setState(() => _saving = false);
      }
    }
  }

  void _showMessage(String message) {
    ScaffoldMessenger.of(
      context,
    ).showSnackBar(SnackBar(content: Text(message)));
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: Text(
          _editing ? AppStrings.editQuickNote : AppStrings.quickNoteListTitle,
        ),
        actions: <Widget>[
          if (_editing)
            TextButton(
              onPressed: _saving ? null : _save,
              child: Text(
                _saving ? AppStrings.loading : AppStrings.saveChanges,
              ),
            )
          else
            IconButton(
              onPressed: () => setState(() => _editing = true),
              icon: const Icon(Icons.edit_outlined),
              tooltip: AppStrings.edit,
            ),
          PopupMenuButton<String>(
            onSelected: (value) {
              if (value == 'delete') {
                _delete();
              }
            },
            itemBuilder: (context) => const <PopupMenuEntry<String>>[
              PopupMenuItem<String>(
                value: 'delete',
                child: ListTile(
                  contentPadding: EdgeInsets.zero,
                  leading: Icon(Icons.delete_outline),
                  title: Text(AppStrings.delete),
                ),
              ),
            ],
          ),
        ],
      ),
      body: ListView(
        padding: const EdgeInsets.all(20),
        children: <Widget>[
          if (_editing) ...<Widget>[
            TextField(
              controller: _contentController,
              minLines: 4,
              maxLines: 10,
              decoration: const InputDecoration(
                labelText: AppStrings.detailsField,
              ),
            ),
            const SizedBox(height: 16),
            TagInputField(
              initialTags: _tags,
              onChanged: (value) => _tags = value,
            ),
            const SizedBox(height: 16),
          ] else ...<Widget>[
            MarkdownBody(
              data: _item.content,
              selectable: true,
              styleSheet: MarkdownStyleSheet.fromTheme(Theme.of(context)),
            ),
            const SizedBox(height: 20),
            _DetailLine(
              label: AppStrings.tagsField,
              child: _item.tags.isEmpty
                  ? const Text(AppStrings.noContent)
                  : Wrap(
                      spacing: 8,
                      runSpacing: 8,
                      children: _item.tags
                          .map((tag) => Chip(label: Text(tag)))
                          .toList(),
                    ),
            ),
            _DetailLine(
              label: AppStrings.selectedAttachments,
              child: Text('${_item.sourceAttachmentIds.length}'),
            ),
            _DetailLine(
              label: AppStrings.createTimeField,
              child: Text(formatDateTime(_item.createdAt)),
            ),
          ],
          if (_editing) ...<Widget>[
            _DetailLine(
              label: AppStrings.selectedAttachments,
              child: Text('${_item.sourceAttachmentIds.length}'),
            ),
            _DetailLine(
              label: AppStrings.createTimeField,
              child: Text(formatDateTime(_item.createdAt)),
            ),
          ],
        ],
      ),
    );
  }
}

class _DetailLine extends StatelessWidget {
  const _DetailLine({required this.label, required this.child});

  final String label;
  final Widget child;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 16),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Text(label, style: Theme.of(context).textTheme.labelMedium),
          const SizedBox(height: 6),
          child,
        ],
      ),
    );
  }
}
