import 'package:flutter/material.dart';

import '../app_controller.dart';
import '../attachment_picker.dart';
import '../date_utils.dart';
import '../models.dart';
import '../strings.dart';
import 'quick_note_list_page.dart';
import 'schedule_list_page.dart';
import 'settings_page.dart';

class ChatHomePage extends StatefulWidget {
  const ChatHomePage({super.key, required this.controller});

  final AppController controller;

  @override
  State<ChatHomePage> createState() => _ChatHomePageState();
}

class _ChatHomePageState extends State<ChatHomePage> {
  late final TextEditingController _textController;
  late final ScrollController _scrollController;
  final List<LocalAttachmentData> _attachments = <LocalAttachmentData>[];
  InputSourceType _sourceType = InputSourceType.text;
  bool _sending = false;
  bool _bootstrapped = false;

  @override
  void initState() {
    super.initState();
    _textController = TextEditingController();
    _scrollController = ScrollController();
    WidgetsBinding.instance.addPostFrameCallback((_) => _bootstrap());
  }

  @override
  void dispose() {
    _textController.dispose();
    _scrollController.dispose();
    super.dispose();
  }

  Future<void> _bootstrap() async {
    if (_bootstrapped || !widget.controller.isAuthenticated) {
      return;
    }
    _bootstrapped = true;
    try {
      await widget.controller.ensureConversationReady();
      _scrollToBottom();
    } catch (error) {
      _showMessage(error.toString());
    }
  }

  Future<void> _openSettings() async {
    Navigator.of(context).pop();
    await Navigator.of(context).push<void>(
      MaterialPageRoute<void>(
        builder: (_) => SettingsPage(controller: widget.controller),
      ),
    );
  }

  Future<void> _openSchedules() async {
    Navigator.of(context).pop();
    await Navigator.of(context).push<void>(
      MaterialPageRoute<void>(
        builder: (_) => ScheduleListPage(controller: widget.controller),
      ),
    );
  }

  Future<void> _openQuickNotes() async {
    Navigator.of(context).pop();
    await Navigator.of(context).push<void>(
      MaterialPageRoute<void>(
        builder: (_) => QuickNoteListPage(controller: widget.controller),
      ),
    );
  }

  Future<void> _createNewConversation() async {
    Navigator.of(context).pop();
    try {
      await widget.controller.createConversationAndSelect();
      _scrollToBottom();
    } catch (error) {
      _showMessage(error.toString());
    }
  }

  Future<void> _selectConversation(int conversationId) async {
    Navigator.of(context).pop();
    try {
      await widget.controller.selectConversation(conversationId);
      _scrollToBottom();
    } catch (error) {
      _showMessage(error.toString());
    }
  }

  Future<void> _showInputTypePicker() async {
    final selected = await showModalBottomSheet<InputSourceType>(
      context: context,
      builder: (context) => SafeArea(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: InputSourceType.values
              .map(
                (type) => ListTile(
                  leading: Icon(_sourceTypeIcon(type)),
                  title: Text(AppStrings.sourceLabel(type)),
                  onTap: () => Navigator.of(context).pop(type),
                ),
              )
              .toList(),
        ),
      ),
    );
    if (selected != null && mounted) {
      setState(() => _sourceType = selected);
    }
  }

  Future<void> _showAttachmentPicker() async {
    final action = await showModalBottomSheet<_AttachmentAction>(
      context: context,
      builder: (context) => SafeArea(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: <Widget>[
            ListTile(
              leading: const Icon(Icons.image_outlined),
              title: const Text(AppStrings.importScreenshot),
              onTap: () => Navigator.of(context).pop(_AttachmentAction.screenshot),
            ),
            ListTile(
              leading: const Icon(Icons.photo_camera_outlined),
              title: const Text(AppStrings.importPhoto),
              onTap: () => Navigator.of(context).pop(_AttachmentAction.photo),
            ),
            ListTile(
              leading: const Icon(Icons.forum_outlined),
              title: const Text(AppStrings.importChatFile),
              onTap: () => Navigator.of(context).pop(_AttachmentAction.chatRecord),
            ),
            ListTile(
              leading: const Icon(Icons.mail_outline),
              title: const Text(AppStrings.importEmailFile),
              onTap: () => Navigator.of(context).pop(_AttachmentAction.email),
            ),
          ],
        ),
      ),
    );
    if (action == null) {
      return;
    }
    try {
      switch (action) {
        case _AttachmentAction.screenshot:
          final files = await AttachmentPicker.pickFiles(
            allowedExtensions: <String>['png', 'jpg', 'jpeg', 'pdf'],
          );
          if (!mounted || files.isEmpty) {
            return;
          }
          setState(() {
            _sourceType = InputSourceType.screenshot;
            _attachments.addAll(files);
          });
          break;
        case _AttachmentAction.photo:
          final file = await AttachmentPicker.pickPhoto();
          if (!mounted || file == null) {
            return;
          }
          setState(() {
            _sourceType = InputSourceType.photo;
            _attachments.add(file);
          });
          break;
        case _AttachmentAction.chatRecord:
          final files = await AttachmentPicker.pickFiles(
            allowedExtensions: <String>['txt', 'json', 'png', 'jpg', 'jpeg', 'pdf'],
          );
          if (!mounted || files.isEmpty) {
            return;
          }
          setState(() {
            _sourceType = InputSourceType.chatRecord;
            _attachments.addAll(files);
          });
          break;
        case _AttachmentAction.email:
          final files = await AttachmentPicker.pickFiles(
            allowedExtensions: <String>['eml', 'png', 'jpg', 'jpeg', 'pdf'],
          );
          if (!mounted || files.isEmpty) {
            return;
          }
          setState(() {
            _sourceType = InputSourceType.email;
            _attachments.addAll(files);
          });
          break;
      }
    } catch (error) {
      _showMessage(error.toString());
    }
  }

  Future<void> _sendMessage() async {
    final text = _textController.text.trim();
    if (text.isEmpty && _attachments.isEmpty) {
      _showMessage(AppStrings.sendEmptyMessage);
      return;
    }
    setState(() => _sending = true);
    try {
      final uploadedIds = <int>[];
      for (final attachment in _attachments) {
        final uploaded = await widget.controller.uploadAttachment(_sourceType, attachment);
        uploadedIds.add(uploaded.attachmentId);
      }
      await widget.controller.sendChatMessage(
        textContent: text,
        sourceType: _sourceType,
        attachmentIds: uploadedIds,
      );
      if (!mounted) {
        return;
      }
      setState(() {
        _textController.clear();
        _attachments.clear();
        _sourceType = InputSourceType.text;
      });
      _scrollToBottom();
    } catch (error) {
      _showMessage(error.toString());
    } finally {
      if (mounted) {
        setState(() => _sending = false);
      }
    }
  }

  Future<void> _performAction(String action, {Map<String, dynamic> payload = const <String, dynamic>{}}) async {
    try {
      await widget.controller.performConversationAction(action: action, payload: payload);
      _scrollToBottom();
    } catch (error) {
      _showMessage(error.toString());
    }
  }

  void _showVoiceComingSoon() {
    _showMessage(AppStrings.voiceComingSoon);
  }

  void _showMessage(String message) {
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(message)));
  }

  void _scrollToBottom() {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!_scrollController.hasClients) {
        return;
      }
      _scrollController.animateTo(
        _scrollController.position.maxScrollExtent,
        duration: const Duration(milliseconds: 240),
        curve: Curves.easeOut,
      );
    });
  }

  IconData _sourceTypeIcon(InputSourceType type) {
    switch (type) {
      case InputSourceType.text:
        return Icons.edit_outlined;
      case InputSourceType.screenshot:
        return Icons.image_outlined;
      case InputSourceType.photo:
        return Icons.photo_camera_outlined;
      case InputSourceType.chatRecord:
        return Icons.forum_outlined;
      case InputSourceType.email:
        return Icons.mail_outline;
    }
  }

  @override
  Widget build(BuildContext context) {
    return AnimatedBuilder(
      animation: widget.controller,
      builder: (context, _) {
        final user = widget.controller.session?.user;
        final messages = widget.controller.messages;
        return Scaffold(
          appBar: AppBar(
            title: const Text(AppStrings.appTitle),
          ),
          drawer: Drawer(
            child: SafeArea(
              child: Column(
                children: <Widget>[
                  Padding(
                    padding: const EdgeInsets.fromLTRB(16, 16, 8, 12),
                    child: Row(
                      children: <Widget>[
                        Expanded(
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: <Widget>[
                              Text(
                                user?.displayName ?? AppStrings.noContent,
                                style: Theme.of(context).textTheme.titleLarge,
                              ),
                              const SizedBox(height: 4),
                              Text(
                                user?.email ?? '',
                                style: Theme.of(context).textTheme.bodySmall,
                              ),
                            ],
                          ),
                        ),
                        IconButton(
                          onPressed: _openSettings,
                          icon: const Icon(Icons.settings_outlined),
                          tooltip: AppStrings.settings,
                        ),
                      ],
                    ),
                  ),
                  const Divider(height: 1),
                  ListTile(
                    leading: const Icon(Icons.event_note_outlined),
                    title: const Text(AppStrings.mySchedules),
                    trailing: Text(widget.controller.schedules.length.toString()),
                    onTap: _openSchedules,
                  ),
                  ListTile(
                    leading: const Icon(Icons.sticky_note_2_outlined),
                    title: const Text(AppStrings.myQuickNotes),
                    trailing: Text(widget.controller.quickNotes.length.toString()),
                    onTap: _openQuickNotes,
                  ),
                  const Divider(height: 1),
                  Padding(
                    padding: const EdgeInsets.fromLTRB(16, 12, 16, 8),
                    child: Row(
                      children: <Widget>[
                        Expanded(
                          child: Text(
                            AppStrings.conversationHistory,
                            style: Theme.of(context).textTheme.titleMedium,
                          ),
                        ),
                        FilledButton.tonalIcon(
                          onPressed: _createNewConversation,
                          icon: const Icon(Icons.add),
                          label: const Text(AppStrings.newConversation),
                        ),
                      ],
                    ),
                  ),
                  Expanded(
                    child: widget.controller.conversations.isEmpty
                        ? const Center(child: Text(AppStrings.emptyConversationHistory))
                        : ListView.builder(
                            itemCount: widget.controller.conversations.length,
                            itemBuilder: (context, index) {
                              final item = widget.controller.conversations[index];
                              final selected = item.id == widget.controller.activeConversationId;
                              return ListTile(
                                selected: selected,
                                leading: const Icon(Icons.chat_bubble_outline),
                                title: Text(
                                  item.title,
                                  maxLines: 1,
                                  overflow: TextOverflow.ellipsis,
                                ),
                                subtitle: Text(formatDateTime(item.lastMessageAt)),
                                onTap: () => _selectConversation(item.id),
                              );
                            },
                          ),
                  ),
                ],
              ),
            ),
          ),
          body: Column(
            children: <Widget>[
              Expanded(
                child: widget.controller.isConversationLoading && messages.isEmpty
                    ? const Center(child: CircularProgressIndicator())
                    : messages.isEmpty
                        ? const Center(child: Text(AppStrings.emptyConversation))
                        : ListView.builder(
                            controller: _scrollController,
                            padding: const EdgeInsets.fromLTRB(16, 16, 16, 8),
                            itemCount: messages.length,
                            itemBuilder: (context, index) {
                              final message = messages[index];
                              return _ConversationMessageView(
                                message: message,
                                onAction: _performAction,
                              );
                            },
                          ),
              ),
              SafeArea(
                top: false,
                child: Container(
                  padding: const EdgeInsets.fromLTRB(16, 8, 16, 16),
                  decoration: const BoxDecoration(
                    color: Colors.white,
                    border: Border(top: BorderSide(color: Color(0xFFE2ECE8))),
                  ),
                  child: Column(
                    mainAxisSize: MainAxisSize.min,
                    children: <Widget>[
                      Row(
                        children: <Widget>[
                          Expanded(
                            child: Text(
                              '${AppStrings.currentInputType}：${AppStrings.sourceLabel(_sourceType)}',
                              style: Theme.of(context).textTheme.bodySmall,
                            ),
                          ),
                          TextButton(
                            onPressed: _sending ? null : _showInputTypePicker,
                            child: const Text(AppStrings.chooseInputType),
                          ),
                        ],
                      ),
                      if (_attachments.isNotEmpty)
                        Align(
                          alignment: Alignment.centerLeft,
                          child: Wrap(
                            spacing: 8,
                            runSpacing: 8,
                            children: _attachments.asMap().entries.map((entry) {
                              return InputChip(
                                label: Text(entry.value.fileName),
                                onDeleted: _sending
                                    ? null
                                    : () {
                                        setState(() {
                                          _attachments.removeAt(entry.key);
                                        });
                                      },
                              );
                            }).toList(),
                          ),
                        ),
                      const SizedBox(height: 8),
                      Row(
                        crossAxisAlignment: CrossAxisAlignment.end,
                        children: <Widget>[
                          IconButton(
                            onPressed: _sending ? null : _showAttachmentPicker,
                            tooltip: AppStrings.attach,
                            icon: const Icon(Icons.attach_file),
                          ),
                          Expanded(
                            child: TextField(
                              controller: _textController,
                              minLines: 1,
                              maxLines: 5,
                              decoration: const InputDecoration(
                                hintText: AppStrings.composerHint,
                              ),
                            ),
                          ),
                          IconButton(
                            onPressed: _sending ? null : _showVoiceComingSoon,
                            tooltip: AppStrings.voice,
                            icon: const Icon(Icons.mic_none),
                          ),
                          const SizedBox(width: 4),
                          FilledButton(
                            onPressed: _sending ? null : _sendMessage,
                            child: Text(_sending ? AppStrings.loading : AppStrings.send),
                          ),
                        ],
                      ),
                    ],
                  ),
                ),
              ),
            ],
          ),
        );
      },
    );
  }
}

enum _AttachmentAction { screenshot, photo, chatRecord, email }

class _ConversationMessageView extends StatelessWidget {
  const _ConversationMessageView({
    required this.message,
    required this.onAction,
  });

  final ConversationMessageItem message;
  final Future<void> Function(String action, {Map<String, dynamic> payload}) onAction;

  @override
  Widget build(BuildContext context) {
    if (message.messageType == 'text') {
      final isUser = message.isUser;
      return Align(
        alignment: isUser ? Alignment.centerRight : Alignment.centerLeft,
        child: Container(
          constraints: const BoxConstraints(maxWidth: 560),
          margin: const EdgeInsets.only(bottom: 12),
          padding: const EdgeInsets.all(14),
          decoration: BoxDecoration(
            color: isUser ? const Color(0xFF176B5A) : Colors.white,
            borderRadius: BorderRadius.circular(18),
            boxShadow: const <BoxShadow>[
              BoxShadow(
                color: Color(0x14000000),
                blurRadius: 12,
                offset: Offset(0, 4),
              ),
            ],
          ),
          child: Text(
            message.textContent ?? '',
            style: TextStyle(color: isUser ? Colors.white : const Color(0xFF173C35), height: 1.55),
          ),
        ),
      );
    }

    return Align(
      alignment: Alignment.centerLeft,
      child: Container(
        constraints: const BoxConstraints(maxWidth: 620),
        margin: const EdgeInsets.only(bottom: 12),
        child: _StructuredMessageCard(
          message: message,
          onAction: onAction,
        ),
      ),
    );
  }
}

class _StructuredMessageCard extends StatelessWidget {
  const _StructuredMessageCard({
    required this.message,
    required this.onAction,
  });

  final ConversationMessageItem message;
  final Future<void> Function(String action, {Map<String, dynamic> payload}) onAction;

  @override
  Widget build(BuildContext context) {
    switch (message.messageType) {
      case 'schedule_draft_card':
        return _ScheduleDraftCard(message: message, onAction: onAction);
      case 'conflict_card':
        return _ConflictCard(message: message);
      case 'quick_note_preview_card':
        return _QuickNotePreviewCard(message: message, onAction: onAction);
      case 'result_card':
        return _ResultCard(message: message);
      default:
        return const SizedBox.shrink();
    }
  }
}

class _ScheduleDraftCard extends StatefulWidget {
  const _ScheduleDraftCard({
    required this.message,
    required this.onAction,
  });

  final ConversationMessageItem message;
  final Future<void> Function(String action, {Map<String, dynamic> payload}) onAction;

  @override
  State<_ScheduleDraftCard> createState() => _ScheduleDraftCardState();
}

class _ScheduleDraftCardState extends State<_ScheduleDraftCard> {
  late final TextEditingController _titleController;
  late final TextEditingController _timeController;
  late final TextEditingController _locationController;
  late final TextEditingController _detailsController;
  bool _busy = false;

  Map<String, dynamic> get _payload => widget.message.structuredPayload;
  Map<String, dynamic> get _draft => (_payload['draft'] as Map<String, dynamic>? ?? <String, dynamic>{});
  String get _stage => _payload['stage'] as String? ?? 'awaiting_confirmation';

  @override
  void initState() {
    super.initState();
    _titleController = TextEditingController(text: _draft['title'] as String? ?? '');
    _locationController = TextEditingController(text: _draft['location'] as String? ?? '');
    _detailsController = TextEditingController(text: _draft['details'] as String? ?? '');
    final scheduledAt = _draft['scheduled_at'] as String?;
    final parsed = scheduledAt == null ? null : DateTime.tryParse(scheduledAt);
    _timeController = TextEditingController(text: formatDateTime(parsed));
    if (parsed == null) {
      _timeController.text = '';
    }
  }

  @override
  void dispose() {
    _titleController.dispose();
    _timeController.dispose();
    _locationController.dispose();
    _detailsController.dispose();
    super.dispose();
  }

  Future<void> _submitMissingFields() async {
    final parsed = _timeController.text.trim().isEmpty ? null : parseEditableDateTime(_timeController.text.trim());
    if (_timeController.text.trim().isNotEmpty && parsed == null) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('时间格式无法识别，请输入如 2026-05-23 14:30 的格式。')),
      );
      return;
    }
    setState(() => _busy = true);
    try {
      await widget.onAction(
        'submit_missing_fields',
        payload: <String, dynamic>{
          'title': _titleController.text.trim(),
          'scheduled_at': parsed?.toLocal().toIso8601String(),
          'location': _locationController.text.trim(),
          'details': _detailsController.text.trim(),
        },
      );
    } finally {
      if (mounted) {
        setState(() => _busy = false);
      }
    }
  }

  Future<void> _confirm() async {
    setState(() => _busy = true);
    try {
      await widget.onAction('confirm_schedule_draft');
    } finally {
      if (mounted) {
        setState(() => _busy = false);
      }
    }
  }

  Future<void> _dismiss() async {
    setState(() => _busy = true);
    try {
      await widget.onAction('dismiss_pending_action');
    } finally {
      if (mounted) {
        setState(() => _busy = false);
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final missingFields = (_payload['missing_fields'] as List<dynamic>? ?? <dynamic>[]).cast<String>();
    final ambiguityFlags = (_payload['ambiguity_flags'] as List<dynamic>? ?? <dynamic>[]).cast<String>();
    final evidenceDigest = (_payload['evidence_digest'] as List<dynamic>? ?? <dynamic>[]).cast<String>();
    final parseConfidence = (_payload['parse_confidence'] as num?)?.toDouble() ?? 0;
    final isEditing = _stage == 'awaiting_missing_fields';

    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            Text(AppStrings.scheduleDraft, style: Theme.of(context).textTheme.titleMedium),
            const SizedBox(height: 16),
            TextField(
              controller: _titleController,
              enabled: isEditing && !_busy,
              decoration: const InputDecoration(labelText: AppStrings.titleField),
            ),
            const SizedBox(height: 12),
            TextField(
              controller: _timeController,
              enabled: isEditing && !_busy,
              decoration: const InputDecoration(labelText: AppStrings.timeField),
            ),
            const SizedBox(height: 12),
            TextField(
              controller: _locationController,
              enabled: isEditing && !_busy,
              decoration: const InputDecoration(labelText: AppStrings.locationField),
            ),
            const SizedBox(height: 12),
            TextField(
              controller: _detailsController,
              enabled: isEditing && !_busy,
              minLines: 2,
              maxLines: 4,
              decoration: const InputDecoration(labelText: AppStrings.detailsField),
            ),
            const SizedBox(height: 12),
            _SectionChips(
              title: AppStrings.missingFieldsField,
              values: missingFields.map(AppStrings.missingFieldLabel).toList(),
            ),
            _SectionChips(
              title: AppStrings.ambiguityField,
              values: ambiguityFlags.map(AppStrings.ambiguityLabel).toList(),
            ),
            _SectionList(
              title: AppStrings.evidenceField,
              values: evidenceDigest,
            ),
            const SizedBox(height: 8),
            Text('${AppStrings.parseConfidenceField}：${(parseConfidence * 100).toStringAsFixed(0)}%'),
            const SizedBox(height: 16),
            Row(
              children: <Widget>[
                Expanded(
                  child: OutlinedButton(
                    onPressed: _busy ? null : _dismiss,
                    child: const Text(AppStrings.cancelPendingAction),
                  ),
                ),
                const SizedBox(width: 12),
                Expanded(
                  child: FilledButton(
                    onPressed: _busy ? null : (isEditing ? _submitMissingFields : _confirm),
                    child: Text(
                      _busy
                          ? AppStrings.loading
                          : isEditing
                              ? AppStrings.submitMissingFields
                              : AppStrings.confirmSave,
                    ),
                  ),
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }
}

class _ConflictCard extends StatelessWidget {
  const _ConflictCard({required this.message});

  final ConversationMessageItem message;

  @override
  Widget build(BuildContext context) {
    final payload = message.structuredPayload;
    final conflicts = (payload['conflict_items'] as List<dynamic>? ?? <dynamic>[])
        .map((item) => Map<String, dynamic>.from(item as Map))
        .toList();
    final suggestions = (payload['suggestions'] as List<dynamic>? ?? <dynamic>[])
        .map((item) => Map<String, dynamic>.from(item as Map))
        .toList();
    final riskLevel = payload['risk_level'] as String? ?? 'low';

    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            Text(AppStrings.conflictCheck, style: Theme.of(context).textTheme.titleMedium),
            const SizedBox(height: 12),
            Text('${AppStrings.riskLevelField}：${AppStrings.riskLevelLabel(riskLevel)}'),
            const SizedBox(height: 12),
            _SectionList(
              title: AppStrings.conflictItemsField,
              values: conflicts.isEmpty
                  ? const <String>['未发现冲突']
                  : conflicts.map((item) {
                      final title = item['title'] as String? ?? '';
                      final start = DateTime.tryParse(item['starts_at'] as String? ?? '');
                      final end = DateTime.tryParse(item['ends_at'] as String? ?? '');
                      return '$title（${formatDateTime(start)} - ${formatDateTime(end)}）';
                    }).toList(),
            ),
            _SectionList(
              title: AppStrings.suggestionsField,
              values: suggestions.isEmpty
                  ? const <String>['暂无建议时段']
                  : suggestions.map((item) {
                      final label = item['label'] as String? ?? '';
                      final start = DateTime.tryParse(item['candidate_start'] as String? ?? '');
                      final end = DateTime.tryParse(item['candidate_end'] as String? ?? '');
                      return '$label：${formatDateTime(start)} - ${formatDateTime(end)}';
                    }).toList(),
            ),
          ],
        ),
      ),
    );
  }
}

class _QuickNotePreviewCard extends StatefulWidget {
  const _QuickNotePreviewCard({
    required this.message,
    required this.onAction,
  });

  final ConversationMessageItem message;
  final Future<void> Function(String action, {Map<String, dynamic> payload}) onAction;

  @override
  State<_QuickNotePreviewCard> createState() => _QuickNotePreviewCardState();
}

class _QuickNotePreviewCardState extends State<_QuickNotePreviewCard> {
  bool _busy = false;

  Future<void> _confirm() async {
    setState(() => _busy = true);
    try {
      await widget.onAction('confirm_quick_note');
    } finally {
      if (mounted) {
        setState(() => _busy = false);
      }
    }
  }

  Future<void> _dismiss() async {
    setState(() => _busy = true);
    try {
      await widget.onAction('dismiss_pending_action');
    } finally {
      if (mounted) {
        setState(() => _busy = false);
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final payload = widget.message.structuredPayload;
    final tags = (payload['preview_tags'] as List<dynamic>? ?? <dynamic>[]).cast<String>();
    final evidenceDigest = (payload['evidence_digest'] as List<dynamic>? ?? <dynamic>[]).cast<String>();

    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            Text(AppStrings.quickNotePreview, style: Theme.of(context).textTheme.titleMedium),
            const SizedBox(height: 12),
            Text(payload['normalized_content'] as String? ?? ''),
            const SizedBox(height: 12),
            _SectionChips(title: AppStrings.tagsField, values: tags),
            _SectionList(title: AppStrings.evidenceField, values: evidenceDigest),
            const SizedBox(height: 16),
            Row(
              children: <Widget>[
                Expanded(
                  child: OutlinedButton(
                    onPressed: _busy ? null : _dismiss,
                    child: const Text(AppStrings.cancelPendingAction),
                  ),
                ),
                const SizedBox(width: 12),
                Expanded(
                  child: FilledButton(
                    onPressed: _busy ? null : _confirm,
                    child: Text(_busy ? AppStrings.loading : AppStrings.confirmSave),
                  ),
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }
}

class _ResultCard extends StatelessWidget {
  const _ResultCard({required this.message});

  final ConversationMessageItem message;

  @override
  Widget build(BuildContext context) {
    final payload = message.structuredPayload;
    final resultKind = payload['result_kind'] as String? ?? '';
    final summary = payload['summary'] as String? ?? AppStrings.chatActionSummary(resultKind);
    final channels = (payload['channels'] as List<dynamic>? ?? <dynamic>[]).cast<String>();

    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            Text(AppStrings.resultCard, style: Theme.of(context).textTheme.titleMedium),
            const SizedBox(height: 12),
            Text(summary),
            if (payload['title'] is String) ...<Widget>[
              const SizedBox(height: 8),
              Text('${AppStrings.titleField}：${payload['title']}'),
            ],
            if (payload['scheduled_at'] is String) ...<Widget>[
              const SizedBox(height: 8),
              Text('${AppStrings.timeField}：${formatDateTime(DateTime.tryParse(payload['scheduled_at'] as String))}'),
            ],
            if (payload['content'] is String) ...<Widget>[
              const SizedBox(height: 8),
              Text('${AppStrings.detailsField}：${payload['content']}'),
            ],
            if ((payload['tags'] as List<dynamic>? ?? <dynamic>[]).isNotEmpty) ...<Widget>[
              const SizedBox(height: 8),
              _SectionChips(
                title: AppStrings.tagsField,
                values: (payload['tags'] as List<dynamic>).cast<String>(),
              ),
            ],
            if (channels.isNotEmpty) ...<Widget>[
              const SizedBox(height: 8),
              _SectionChips(
                title: AppStrings.notificationHistory,
                values: channels.map(AppStrings.channelLabel).toList(),
              ),
            ],
          ],
        ),
      ),
    );
  }
}

class _SectionChips extends StatelessWidget {
  const _SectionChips({
    required this.title,
    required this.values,
  });

  final String title;
  final List<String> values;

  @override
  Widget build(BuildContext context) {
    if (values.isEmpty) {
      return const SizedBox.shrink();
    }
    return Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Text(title, style: Theme.of(context).textTheme.labelLarge),
          const SizedBox(height: 8),
          Wrap(
            spacing: 8,
            runSpacing: 8,
            children: values.map((item) => Chip(label: Text(item))).toList(),
          ),
        ],
      ),
    );
  }
}

class _SectionList extends StatelessWidget {
  const _SectionList({
    required this.title,
    required this.values,
  });

  final String title;
  final List<String> values;

  @override
  Widget build(BuildContext context) {
    if (values.isEmpty) {
      return const SizedBox.shrink();
    }
    return Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Text(title, style: Theme.of(context).textTheme.labelLarge),
          const SizedBox(height: 8),
          ...values.map((item) => Padding(
                padding: const EdgeInsets.only(bottom: 4),
                child: Text('• $item'),
              )),
        ],
      ),
    );
  }
}
