import 'package:flutter/material.dart';

import '../app_controller.dart';
import '../date_utils.dart';
import '../models.dart';

class QuickNotePage extends StatefulWidget {
  const QuickNotePage({super.key, required this.controller});

  final AppController controller;

  @override
  State<QuickNotePage> createState() => _QuickNotePageState();
}

class _QuickNotePageState extends State<QuickNotePage> {
  late final TextEditingController _contentController;
  late final TextEditingController _tagsController;

  QuickNotePreview? _preview;
  bool _busy = false;

  @override
  void initState() {
    super.initState();
    _contentController = TextEditingController();
    _tagsController = TextEditingController();
  }

  @override
  void dispose() {
    _contentController.dispose();
    _tagsController.dispose();
    super.dispose();
  }

  List<String> get _manualTags => _tagsController.text
      .split(',')
      .map((item) => item.trim())
      .where((item) => item.isNotEmpty)
      .toList();

  Future<void> _previewSave() async {
    if (_contentController.text.trim().isEmpty) {
      _showMessage('Write a quick note first.');
      return;
    }
    setState(() => _busy = true);
    try {
      final preview = await widget.controller.previewQuickNote(
        _contentController.text.trim(),
        _manualTags,
      );
      setState(() => _preview = preview);
    } catch (error) {
      _showMessage(error.toString());
    } finally {
      if (mounted) {
        setState(() => _busy = false);
      }
    }
  }

  Future<void> _confirmSave() async {
    final preview = _preview;
    if (preview == null) {
      _showMessage('Preview tags before saving.');
      return;
    }
    setState(() => _busy = true);
    try {
      await widget.controller.confirmQuickNote(
        _contentController.text.trim(),
        preview.previewTags,
        preview.approval.approvalToken,
      );
      setState(() {
        _preview = null;
        _contentController.clear();
        _tagsController.clear();
      });
      _showMessage('Quick note saved.');
    } catch (error) {
      _showMessage(error.toString());
    } finally {
      if (mounted) {
        setState(() => _busy = false);
      }
    }
  }

  void _showAttachmentPlaceholder() {
    _showMessage(
      'Attachment capture comes later. This MVP stores text only.',
    );
  }

  void _showMessage(String message) {
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(content: Text(message)),
    );
  }

  @override
  Widget build(BuildContext context) {
    return AnimatedBuilder(
      animation: widget.controller,
      builder: (context, _) => Scaffold(
        appBar: AppBar(title: const Text('Quick note')),
        body: ListView(
          padding: const EdgeInsets.all(16),
          children: <Widget>[
            TextField(
              controller: _contentController,
              maxLines: 6,
              decoration: InputDecoration(
                labelText: 'Idea, thought, or temporary task',
                hintText:
                    'Example: Prepare the experiment checklist and update the paper figures by Friday',
                suffixIcon: IconButton(
                  onPressed: _showAttachmentPlaceholder,
                  icon: const Icon(Icons.attach_file),
                ),
              ),
            ),
            const SizedBox(height: 12),
            TextField(
              controller: _tagsController,
              decoration: const InputDecoration(
                labelText: 'Manual tags (optional)',
                hintText: 'teaching, research',
              ),
            ),
            const SizedBox(height: 16),
            Row(
              children: <Widget>[
                Expanded(
                  child: FilledButton.tonal(
                    onPressed: _busy ? null : _previewSave,
                    child: Text(_busy ? 'Working...' : 'Preview tags'),
                  ),
                ),
                const SizedBox(width: 12),
                Expanded(
                  child: FilledButton(
                    onPressed: (_busy || _preview == null) ? null : _confirmSave,
                    child: const Text('Approve and save'),
                  ),
                ),
              ],
            ),
            if (_preview != null) ...<Widget>[
              const SizedBox(height: 16),
              Card(
                child: Padding(
                  padding: const EdgeInsets.all(16),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: <Widget>[
                      Text(
                        'Save preview',
                        style: Theme.of(context).textTheme.titleMedium,
                      ),
                      const SizedBox(height: 12),
                      Wrap(
                        spacing: 8,
                        children: _preview!.previewTags
                            .map((item) => Chip(label: Text(item)))
                            .toList(),
                      ),
                      const SizedBox(height: 12),
                      Text(
                        'Approval expires at: ${formatDateTime(_preview!.approval.expiresAt)}',
                      ),
                    ],
                  ),
                ),
              ),
            ],
            const SizedBox(height: 24),
            Text(
              'Recent notes',
              style: Theme.of(context).textTheme.titleLarge,
            ),
            const SizedBox(height: 12),
            if (widget.controller.quickNotes.isEmpty)
              const _QuickNoteEmpty()
            else
              ...widget.controller.quickNotes.map(_QuickNoteTile.new),
          ],
        ),
      ),
    );
  }
}

class _QuickNoteTile extends StatelessWidget {
  const _QuickNoteTile(this.item);

  final QuickNoteItem item;

  @override
  Widget build(BuildContext context) {
    return Card(
      margin: const EdgeInsets.only(bottom: 12),
      child: ListTile(
        title: Text(item.content),
        subtitle: Text(
          '${item.tags.join(' / ')}\n${formatDateTime(item.createdAt)}',
        ),
        isThreeLine: true,
      ),
    );
  }
}

class _QuickNoteEmpty extends StatelessWidget {
  const _QuickNoteEmpty();

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(20),
      decoration: BoxDecoration(
        color: const Color(0xFFF5FAF8),
        borderRadius: BorderRadius.circular(20),
      ),
      child: const Text(
        'No quick notes yet. Capture one idea, then approve the save.',
      ),
    );
  }
}
