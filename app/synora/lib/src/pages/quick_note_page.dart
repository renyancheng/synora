import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import 'package:image_picker/image_picker.dart';

import '../app_controller.dart';
import '../date_utils.dart';
import '../models.dart';
import '../strings.dart';

class QuickNotePage extends StatefulWidget {
  const QuickNotePage({super.key, required this.controller});

  final AppController controller;

  @override
  State<QuickNotePage> createState() => _QuickNotePageState();
}

class _QuickNotePageState extends State<QuickNotePage> {
  late final TextEditingController _contentController;
  late final TextEditingController _tagsController;
  InputSourceType _sourceType = InputSourceType.text;
  final List<LocalAttachmentData> _attachments = <LocalAttachmentData>[];
  QuickNoteDraftPreview? _preview;
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
      .split(RegExp(r'[,，]'))
      .map((item) => item.trim())
      .where((item) => item.isNotEmpty)
      .toList();

  Future<void> _pickFile({List<String>? allowedExtensions}) async {
    final result = await FilePicker.platform.pickFiles(
      allowMultiple: true,
      withData: true,
      type: allowedExtensions == null ? FileType.any : FileType.custom,
      allowedExtensions: allowedExtensions,
    );
    if (result == null) {
      return;
    }
    setState(() {
      for (final file in result.files) {
        if (file.bytes != null) {
          _attachments.add(LocalAttachmentData(fileName: file.name, bytes: file.bytes!));
        }
      }
    });
  }

  Future<void> _pickPhoto() async {
    final picker = ImagePicker();
    final image = await picker.pickImage(source: ImageSource.camera, imageQuality: 75);
    if (image == null) {
      return;
    }
    final bytes = await image.readAsBytes();
    setState(() {
      _attachments.add(LocalAttachmentData(fileName: image.name, bytes: bytes));
    });
  }

  Future<void> _previewSave() async {
    if (_contentController.text.trim().isEmpty && _attachments.isEmpty) {
      _showMessage('请输入速记内容或上传附件。');
      return;
    }
    setState(() => _busy = true);
    try {
      final uploadedIds = <int>[];
      for (final attachment in _attachments) {
        final uploaded = await widget.controller.uploadAttachment(_sourceType, attachment);
        uploadedIds.add(uploaded.attachmentId);
      }
      final preview = await widget.controller.createQuickNoteDraft(
        sourceType: _sourceType,
        content: _contentController.text.trim(),
        tags: _manualTags,
        attachmentIds: uploadedIds,
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
      _showMessage('请先预览标签。');
      return;
    }
    setState(() => _busy = true);
    try {
      await widget.controller.confirmQuickNote(
        content: preview.normalizedContent,
        tags: preview.previewTags,
        sourceType: preview.sourceType,
        attachmentIds: preview.attachmentIds,
        approvalToken: preview.approval.approvalToken,
      );
      setState(() {
        _preview = null;
        _contentController.clear();
        _tagsController.clear();
        _attachments.clear();
      });
      _showMessage('速记已保存。');
    } catch (error) {
      _showMessage(error.toString());
    } finally {
      if (mounted) {
        setState(() => _busy = false);
      }
    }
  }

  void _showMessage(String message) {
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(message)));
  }

  @override
  Widget build(BuildContext context) {
    return AnimatedBuilder(
      animation: widget.controller,
      builder: (context, _) => Scaffold(
        appBar: AppBar(title: const Text('记录速记')),
        body: ListView(
          padding: const EdgeInsets.all(16),
          children: <Widget>[
            Wrap(
              spacing: 8,
              runSpacing: 8,
              children: InputSourceType.values
                  .map(
                    (item) => ChoiceChip(
                      label: Text(AppStrings.sourceLabel(item)),
                      selected: _sourceType == item,
                      onSelected: (_) => setState(() => _sourceType = item),
                    ),
                  )
                  .toList(),
            ),
            const SizedBox(height: 16),
            TextField(
              controller: _contentController,
              maxLines: 6,
              decoration: InputDecoration(
                labelText: _sourceType == InputSourceType.email ? '邮件内容或摘录' : '速记内容',
                hintText: AppStrings.sourceHint(_sourceType),
              ),
            ),
            const SizedBox(height: 12),
            TextField(
              controller: _tagsController,
              decoration: const InputDecoration(
                labelText: '手动标签（可选）',
                hintText: '例如：教学，科研',
              ),
            ),
            const SizedBox(height: 12),
            Wrap(
              spacing: 12,
              runSpacing: 12,
              children: <Widget>[
                if (_sourceType == InputSourceType.photo)
                  OutlinedButton.icon(
                    onPressed: _busy ? null : _pickPhoto,
                    icon: const Icon(Icons.photo_camera_outlined),
                    label: const Text('拍照导入'),
                  )
                else
                  OutlinedButton.icon(
                    onPressed: _busy
                        ? null
                        : () => _pickFile(
                              allowedExtensions: _sourceType == InputSourceType.email
                                  ? <String>['eml', 'png', 'jpg', 'jpeg', 'pdf']
                                  : _sourceType == InputSourceType.chatRecord
                                      ? <String>['txt', 'json', 'png', 'jpg', 'jpeg', 'pdf']
                                      : <String>['png', 'jpg', 'jpeg', 'pdf'],
                            ),
                    icon: const Icon(Icons.attach_file),
                    label: const Text('上传附件'),
                  ),
              ],
            ),
            if (_attachments.isNotEmpty) ...<Widget>[
              const SizedBox(height: 16),
              ..._attachments.asMap().entries.map(
                    (entry) => Card(
                      child: ListTile(
                        title: Text(entry.value.fileName),
                        trailing: IconButton(
                          onPressed: _busy
                              ? null
                              : () => setState(() {
                                    _attachments.removeAt(entry.key);
                                  }),
                          icon: const Icon(Icons.close),
                        ),
                      ),
                    ),
                  ),
            ],
            const SizedBox(height: 16),
            Row(
              children: <Widget>[
                Expanded(
                  child: FilledButton.tonal(
                    onPressed: _busy ? null : _previewSave,
                    child: Text(_busy ? '整理中…' : '预览标签'),
                  ),
                ),
                const SizedBox(width: 12),
                Expanded(
                  child: FilledButton(
                    onPressed: (_busy || _preview == null) ? null : _confirmSave,
                    child: const Text('确认保存'),
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
                      Text('保存预览', style: Theme.of(context).textTheme.titleMedium),
                      const SizedBox(height: 12),
                      Text(_preview!.normalizedContent),
                      const SizedBox(height: 12),
                      Wrap(
                        spacing: 8,
                        children: _preview!.previewTags.map((item) => Chip(label: Text(item))).toList(),
                      ),
                      const SizedBox(height: 12),
                      ..._preview!.evidenceDigest.map((item) => Text('• $item')),
                      const SizedBox(height: 12),
                      Text('审批有效期至：${formatDateTime(_preview!.approval.expiresAt)}'),
                    ],
                  ),
                ),
              ),
            ],
            const SizedBox(height: 24),
            Text('最近速记', style: Theme.of(context).textTheme.titleLarge),
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
        subtitle: Text('${item.tags.join(' / ')}\n${formatDateTime(item.createdAt)}'),
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
      child: const Text(AppStrings.emptyQuickNotes),
    );
  }
}
