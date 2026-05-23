import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import 'package:image_picker/image_picker.dart';

import '../app_controller.dart';
import '../models.dart';
import '../strings.dart';
import 'schedule_confirm_page.dart';

class ScheduleDraftPage extends StatefulWidget {
  const ScheduleDraftPage({super.key, required this.controller});

  final AppController controller;

  @override
  State<ScheduleDraftPage> createState() => _ScheduleDraftPageState();
}

class _ScheduleDraftPageState extends State<ScheduleDraftPage> {
  late final TextEditingController _textController;
  InputSourceType _sourceType = InputSourceType.text;
  final List<LocalAttachmentData> _attachments = <LocalAttachmentData>[];
  bool _submitting = false;

  @override
  void initState() {
    super.initState();
    _textController = TextEditingController();
  }

  @override
  void dispose() {
    _textController.dispose();
    super.dispose();
  }

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

  Future<void> _submit() async {
    final text = _textController.text.trim();
    if (text.isEmpty && _attachments.isEmpty) {
      _showMessage('请输入内容或上传附件。');
      return;
    }
    setState(() => _submitting = true);
    try {
      final uploadedIds = <int>[];
      for (final attachment in _attachments) {
        final uploaded = await widget.controller.uploadAttachment(_sourceType, attachment);
        uploadedIds.add(uploaded.attachmentId);
      }
      final result = await widget.controller.createScheduleDraft(
        sourceType: _sourceType,
        textContent: text,
        attachmentIds: uploadedIds,
      );
      if (!mounted) {
        return;
      }
      await Navigator.of(context).push<void>(
        MaterialPageRoute<void>(
          builder: (_) => ScheduleConfirmPage(
            controller: widget.controller,
            draftResult: result,
          ),
        ),
      );
    } catch (error) {
      _showMessage(error.toString());
    } finally {
      if (mounted) {
        setState(() => _submitting = false);
      }
    }
  }

  void _showMessage(String message) {
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(message)));
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('新增日程')),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: <Widget>[
          Text('输入来源', style: Theme.of(context).textTheme.titleMedium),
          const SizedBox(height: 12),
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
            controller: _textController,
            maxLines: 6,
            decoration: InputDecoration(
              labelText: AppStrings.sourceFieldLabel(_sourceType),
              hintText: AppStrings.sourceHint(_sourceType),
            ),
          ),
          const SizedBox(height: 12),
          Text(
            AppStrings.uploadHint,
            style: Theme.of(context).textTheme.bodySmall?.copyWith(color: const Color(0xFF5B8178)),
          ),
          const SizedBox(height: 12),
          Wrap(
            spacing: 12,
            runSpacing: 12,
            children: <Widget>[
              if (_sourceType == InputSourceType.photo)
                OutlinedButton.icon(
                  onPressed: _submitting ? null : _pickPhoto,
                  icon: const Icon(Icons.photo_camera_outlined),
                  label: const Text('拍照导入'),
                )
              else
                OutlinedButton.icon(
                  onPressed: _submitting
                      ? null
                      : () => _pickFile(
                            allowedExtensions: _sourceType == InputSourceType.email
                                ? <String>['eml', 'png', 'jpg', 'jpeg', 'pdf']
                                : _sourceType == InputSourceType.chatRecord
                                    ? <String>['txt', 'json', 'png', 'jpg', 'jpeg', 'pdf']
                                    : <String>['png', 'jpg', 'jpeg', 'pdf'],
                          ),
                  icon: const Icon(Icons.attach_file),
                  label: Text(
                    _sourceType == InputSourceType.email
                        ? '导入邮件文件'
                        : _sourceType == InputSourceType.chatRecord
                            ? '导入聊天文件'
                            : '上传附件',
                  ),
                ),
            ],
          ),
          if (_attachments.isNotEmpty) ...<Widget>[
            const SizedBox(height: 16),
            Text('待上传附件', style: Theme.of(context).textTheme.titleMedium),
            const SizedBox(height: 8),
            ..._attachments.asMap().entries.map(
                  (entry) => Card(
                    child: ListTile(
                      title: Text(entry.value.fileName),
                      trailing: IconButton(
                        onPressed: _submitting
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
          const SizedBox(height: 20),
          FilledButton(
            onPressed: _submitting ? null : _submit,
            child: Text(_submitting ? '解析中…' : '解析并进入确认页'),
          ),
        ],
      ),
    );
  }
}
