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
  ConversationTool? _selectedTool;
  bool _bootstrapped = false;
  int _lastMessageCount = 0;
  String? _lastShownError;

  @override
  void initState() {
    super.initState();
    _textController = TextEditingController()..addListener(_handleComposerChanged);
    _scrollController = ScrollController();
    WidgetsBinding.instance.addPostFrameCallback((_) => _bootstrap());
  }

  @override
  void dispose() {
    _textController
      ..removeListener(_handleComposerChanged)
      ..dispose();
    _scrollController.dispose();
    super.dispose();
  }

  void _handleComposerChanged() {
    if (mounted) {
      setState(() {});
    }
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

  Future<void> _openComposerMenu() async {
    final result = await showModalBottomSheet<_ComposerMenuResult>(
      context: context,
      showDragHandle: true,
      builder: (context) => _ComposerMenu(selectedTool: _selectedTool),
    );
    if (result == null || !mounted) {
      return;
    }
    if (result.type == _ComposerMenuResultType.selectTool) {
      setState(() => _selectedTool = result.tool);
      return;
    }
    try {
      switch (result.type) {
        case _ComposerMenuResultType.gallery:
          final files = await AttachmentPicker.pickGalleryImages();
          if (files.isNotEmpty && mounted) {
            setState(() => _attachments.addAll(files));
          }
          break;
        case _ComposerMenuResultType.camera:
          final file = await AttachmentPicker.pickPhoto();
          if (file != null && mounted) {
            setState(() => _attachments.add(file));
          }
          break;
        case _ComposerMenuResultType.file:
          final files = await AttachmentPicker.pickFiles();
          if (files.isNotEmpty && mounted) {
            setState(() => _attachments.addAll(files));
          }
          break;
        case _ComposerMenuResultType.selectTool:
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
    final currentAttachments = List<LocalAttachmentData>.from(_attachments);
    final currentTool = _selectedTool;
    setState(() {
      _textController.clear();
      _attachments.clear();
      _selectedTool = null;
    });
    try {
      await widget.controller.sendChatMessage(
        textContent: text,
        attachments: currentAttachments,
        selectedTool: currentTool,
      );
      _scrollToBottom();
    } catch (error) {
      _showMessage(error.toString());
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
        duration: const Duration(milliseconds: 220),
        curve: Curves.easeOut,
      );
    });
  }

  @override
  Widget build(BuildContext context) {
    return AnimatedBuilder(
      animation: widget.controller,
      builder: (context, _) {
        final user = widget.controller.session?.user;
        final messages = widget.controller.messages;
        final latestError = widget.controller.lastError;
        if (messages.length != _lastMessageCount) {
          _lastMessageCount = messages.length;
          _scrollToBottom();
        }
        if (latestError == null || latestError.isEmpty) {
          _lastShownError = null;
        } else if (latestError != _lastShownError) {
          _lastShownError = latestError;
          WidgetsBinding.instance.addPostFrameCallback((_) {
            if (mounted) {
              _showMessage(latestError);
            }
          });
        }
        final hasInput = _textController.text.trim().isNotEmpty || _attachments.isNotEmpty;
        final sending = widget.controller.isMessageSending;

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
                  padding: const EdgeInsets.fromLTRB(16, 10, 16, 16),
                  decoration: const BoxDecoration(
                    color: Colors.white,
                    border: Border(top: BorderSide(color: Color(0xFFE2ECE8))),
                  ),
                  child: Column(
                    mainAxisSize: MainAxisSize.min,
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: <Widget>[
                      if (widget.controller.streamStatusLabel != null) ...<Widget>[
                        Padding(
                          padding: const EdgeInsets.only(bottom: 8),
                          child: Text(
                            widget.controller.streamStatusLabel!,
                            style: Theme.of(context).textTheme.bodySmall?.copyWith(
                                  color: const Color(0xFF4A6C63),
                                ),
                          ),
                        ),
                      ],
                      if (_selectedTool != null) ...<Widget>[
                        Padding(
                          padding: const EdgeInsets.only(bottom: 8),
                          child: InputChip(
                            label: Text('${AppStrings.selectedToolPrefix}：${AppStrings.toolLabel(_selectedTool?.apiValue)}'),
                            onDeleted: sending ? null : () => setState(() => _selectedTool = null),
                          ),
                        ),
                      ],
                      if (_attachments.isNotEmpty) ...<Widget>[
                        Wrap(
                          spacing: 8,
                          runSpacing: 8,
                          children: _attachments.asMap().entries.map((entry) {
                            return InputChip(
                              label: Text(entry.value.fileName),
                              onDeleted: sending
                                  ? null
                                  : () {
                                      setState(() {
                                        _attachments.removeAt(entry.key);
                                      });
                                    },
                            );
                          }).toList(),
                        ),
                        const SizedBox(height: 10),
                      ],
                      Row(
                        crossAxisAlignment: CrossAxisAlignment.end,
                        children: <Widget>[
                          IconButton(
                            onPressed: sending ? null : _openComposerMenu,
                            tooltip: AppStrings.attach,
                            icon: const Icon(Icons.add_circle_outline),
                          ),
                          Expanded(
                            child: TextField(
                              controller: _textController,
                              maxLines: 1,
                              textInputAction: TextInputAction.send,
                              onSubmitted: (_) {
                                if (!sending && hasInput) {
                                  _sendMessage();
                                }
                              },
                              decoration: const InputDecoration(
                                hintText: AppStrings.composerHint,
                              ),
                            ),
                          ),
                          const SizedBox(width: 8),
                          AnimatedSwitcher(
                            duration: const Duration(milliseconds: 220),
                            transitionBuilder: (child, animation) => ScaleTransition(scale: animation, child: child),
                            child: sending
                                ? Container(
                                    key: const ValueKey('loading'),
                                    width: 44,
                                    height: 44,
                                    alignment: Alignment.center,
                                    child: const SizedBox(
                                      width: 20,
                                      height: 20,
                                      child: CircularProgressIndicator(strokeWidth: 2.2),
                                    ),
                                  )
                                : hasInput
                                    ? IconButton.filled(
                                        key: const ValueKey('send'),
                                        onPressed: _sendMessage,
                                        icon: const Icon(Icons.send_rounded),
                                        tooltip: AppStrings.send,
                                      )
                                    : IconButton(
                                        key: const ValueKey('voice'),
                                        onPressed: _showVoiceComingSoon,
                                        icon: const Icon(Icons.mic_none),
                                        tooltip: AppStrings.voice,
                                      ),
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


enum _ComposerMenuResultType { gallery, camera, file, selectTool }


class _ComposerMenuResult {
  const _ComposerMenuResult({
    required this.type,
    this.tool,
  });

  final _ComposerMenuResultType type;
  final ConversationTool? tool;
}


class _ComposerMenu extends StatelessWidget {
  const _ComposerMenu({required this.selectedTool});

  final ConversationTool? selectedTool;

  @override
  Widget build(BuildContext context) {
    return SafeArea(
      child: Padding(
        padding: const EdgeInsets.fromLTRB(16, 8, 16, 16),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            Row(
              children: <Widget>[
                Expanded(
                  child: _AttachmentActionButton(
                    icon: Icons.photo_library_outlined,
                    label: AppStrings.attachmentGallery,
                    onTap: () => Navigator.of(context).pop(
                      const _ComposerMenuResult(type: _ComposerMenuResultType.gallery),
                    ),
                  ),
                ),
                const SizedBox(width: 12),
                Expanded(
                  child: _AttachmentActionButton(
                    icon: Icons.photo_camera_outlined,
                    label: AppStrings.attachmentCamera,
                    onTap: () => Navigator.of(context).pop(
                      const _ComposerMenuResult(type: _ComposerMenuResultType.camera),
                    ),
                  ),
                ),
                const SizedBox(width: 12),
                Expanded(
                  child: _AttachmentActionButton(
                    icon: Icons.folder_open_outlined,
                    label: AppStrings.attachmentFile,
                    onTap: () => Navigator.of(context).pop(
                      const _ComposerMenuResult(type: _ComposerMenuResultType.file),
                    ),
                  ),
                ),
              ],
            ),
            const SizedBox(height: 18),
            ListTile(
              contentPadding: EdgeInsets.zero,
              leading: const Icon(Icons.event_note_outlined),
              title: const Text(AppStrings.scheduleTool),
              subtitle: const Text(AppStrings.scheduleToolDescription),
              trailing: selectedTool == ConversationTool.schedule ? const Icon(Icons.check_circle) : null,
              onTap: () => Navigator.of(context).pop(
                const _ComposerMenuResult(
                  type: _ComposerMenuResultType.selectTool,
                  tool: ConversationTool.schedule,
                ),
              ),
            ),
            ListTile(
              contentPadding: EdgeInsets.zero,
              leading: const Icon(Icons.sticky_note_2_outlined),
              title: const Text(AppStrings.quickNoteTool),
              subtitle: const Text(AppStrings.quickNoteToolDescription),
              trailing: selectedTool == ConversationTool.quickNote ? const Icon(Icons.check_circle) : null,
              onTap: () => Navigator.of(context).pop(
                const _ComposerMenuResult(
                  type: _ComposerMenuResultType.selectTool,
                  tool: ConversationTool.quickNote,
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }
}


class _AttachmentActionButton extends StatelessWidget {
  const _AttachmentActionButton({
    required this.icon,
    required this.label,
    required this.onTap,
  });

  final IconData icon;
  final String label;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return InkWell(
      borderRadius: BorderRadius.circular(20),
      onTap: onTap,
      child: Ink(
        padding: const EdgeInsets.symmetric(vertical: 18),
        decoration: BoxDecoration(
          color: const Color(0xFFF3F8F6),
          borderRadius: BorderRadius.circular(20),
        ),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: <Widget>[
            Icon(icon, size: 28),
            const SizedBox(height: 8),
            Text(label),
          ],
        ),
      ),
    );
  }
}


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
          constraints: const BoxConstraints(maxWidth: 580),
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
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: <Widget>[
              Text(
                message.textContent ?? '',
                style: TextStyle(color: isUser ? Colors.white : const Color(0xFF173C35), height: 1.55),
              ),
              if (message.status == 'sending' || message.status == 'failed' || message.status == 'streaming') ...<Widget>[
                const SizedBox(height: 8),
                Text(
                  _statusLabel(message.status),
                  style: TextStyle(
                    color: isUser ? Colors.white70 : const Color(0xFF617B74),
                    fontSize: 12,
                  ),
                ),
              ],
            ],
          ),
        ),
      );
    }

    return Align(
      alignment: Alignment.centerLeft,
      child: Container(
        constraints: const BoxConstraints(maxWidth: 640),
        margin: const EdgeInsets.only(bottom: 12),
        child: _StructuredMessageCard(
          message: message,
          onAction: onAction,
        ),
      ),
    );
  }

  String _statusLabel(String status) {
    switch (status) {
      case 'sending':
        return AppStrings.sending;
      case 'streaming':
        return '生成中...';
      case 'failed':
        return '发送失败';
      default:
        return '';
    }
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
  late final TextEditingController _startController;
  late final TextEditingController _endController;
  late final TextEditingController _locationController;
  late final TextEditingController _detailsController;
  bool _busy = false;

  Map<String, dynamic> get _payload => widget.message.structuredPayload;
  Map<String, dynamic> get _draft => (_payload['draft'] as Map<String, dynamic>? ?? <String, dynamic>{});

  @override
  void initState() {
    super.initState();
    _titleController = TextEditingController(text: _draft['title'] as String? ?? '');
    _locationController = TextEditingController(text: _draft['location'] as String? ?? '');
    _detailsController = TextEditingController(text: _draft['details'] as String? ?? '');
    final start = _draft['start'] as Map<String, dynamic>?;
    final end = _draft['end'] as Map<String, dynamic>?;
    _startController = TextEditingController(
      text: start == null ? '' : formatDateTime(DateTime.tryParse(start['dateTime'] as String? ?? '')),
    );
    _endController = TextEditingController(
      text: end == null ? '' : formatDateTime(DateTime.tryParse(end['dateTime'] as String? ?? '')),
    );
  }

  @override
  void dispose() {
    _titleController.dispose();
    _startController.dispose();
    _endController.dispose();
    _locationController.dispose();
    _detailsController.dispose();
    super.dispose();
  }

  Future<void> _submitMissingFields() async {
    final parsedStart = _startController.text.trim().isEmpty ? null : parseEditableDateTime(_startController.text.trim());
    final parsedEnd = _endController.text.trim().isEmpty ? null : parseEditableDateTime(_endController.text.trim());
    if (_startController.text.trim().isNotEmpty && parsedStart == null) {
      _showTimeError();
      return;
    }
    if (_endController.text.trim().isNotEmpty && parsedEnd == null) {
      _showTimeError();
      return;
    }
    setState(() => _busy = true);
    try {
      await widget.onAction(
        'submit_missing_fields',
        payload: <String, dynamic>{
          'title': _titleController.text.trim(),
          'start_at': parsedStart?.toLocal().toIso8601String(),
          'end_at': parsedEnd?.toLocal().toIso8601String(),
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

  void _showTimeError() {
    ScaffoldMessenger.of(context).showSnackBar(
      const SnackBar(content: Text('时间格式无法识别，请输入如 2026-05-23 14:30 的格式。')),
    );
  }

  @override
  Widget build(BuildContext context) {
    final missingFields = (_payload['missing_fields'] as List<dynamic>? ?? <dynamic>[]).cast<String>();
    final ambiguityFlags = (_payload['ambiguity_flags'] as List<dynamic>? ?? <dynamic>[]).cast<String>();
    final evidenceDigest = (_payload['evidence_digest'] as List<dynamic>? ?? <dynamic>[]).cast<String>();
    final parseConfidence = (_payload['parse_confidence'] as num?)?.toDouble() ?? 0;
    final stage = _payload['stage'] as String? ?? 'approval_pending';
    final isActionable = _payload['is_actionable'] as bool? ?? false;
    final lifecycle = _payload['lifecycle_status'] as String? ?? stage;
    final recurrence = (_draft['recurrence'] as List<dynamic>? ?? <dynamic>[]).cast<String>();
    final isEditing = stage == 'needs_input' && isActionable;

    return _CardShell(
      title: AppStrings.scheduleDraft,
      lifecycle: lifecycle,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          TextField(
            controller: _titleController,
            enabled: isEditing && !_busy,
            decoration: const InputDecoration(labelText: AppStrings.titleField),
          ),
          const SizedBox(height: 12),
          TextField(
            controller: _startController,
            enabled: isEditing && !_busy,
            decoration: const InputDecoration(labelText: AppStrings.startField),
          ),
          const SizedBox(height: 12),
          TextField(
            controller: _endController,
            enabled: isEditing && !_busy,
            decoration: const InputDecoration(labelText: AppStrings.endField),
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
          _SectionChips(title: AppStrings.missingFieldsField, values: missingFields.map(AppStrings.missingFieldLabel).toList()),
          _SectionChips(title: AppStrings.ambiguityField, values: ambiguityFlags.map(AppStrings.ambiguityLabel).toList()),
          _SectionList(title: AppStrings.evidenceField, values: evidenceDigest),
          _SectionList(title: AppStrings.recurrenceField, values: <String>[formatRecurrence(recurrence)]),
          Text('${AppStrings.parseConfidenceField}：${(parseConfidence * 100).toStringAsFixed(0)}%'),
          if (isActionable) ...<Widget>[
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
                      _busy ? AppStrings.loading : (isEditing ? AppStrings.submitMissingFields : AppStrings.confirmSave),
                    ),
                  ),
                ),
              ],
            ),
          ],
        ],
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
    final lifecycle = payload['lifecycle_status'] as String? ?? 'conflict_review';

    return _CardShell(
      title: AppStrings.conflictCheck,
      lifecycle: lifecycle,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Text('${AppStrings.riskLevelField}：${AppStrings.riskLevelLabel(riskLevel)}'),
          const SizedBox(height: 12),
          _SectionList(
            title: AppStrings.conflictItemsField,
            values: conflicts.isEmpty
                ? const <String>['未发现冲突']
                : conflicts.map((item) {
                    final start = EventDateTimeValue.fromJson(item['start'] as Map<String, dynamic>);
                    final end = EventDateTimeValue.fromJson(item['end'] as Map<String, dynamic>);
                    return '${item['title']}：${formatEventRange(start: start, end: end, isAllDay: false)}';
                  }).toList(),
          ),
          _SectionList(
            title: AppStrings.suggestionsField,
            values: suggestions.isEmpty
                ? const <String>['暂无建议时段']
                : suggestions.map((item) {
                    final start = EventDateTimeValue.fromJson(item['start'] as Map<String, dynamic>);
                    final end = EventDateTimeValue.fromJson(item['end'] as Map<String, dynamic>);
                    return '${item['label']}：${formatEventRange(start: start, end: end, isAllDay: false)}';
                  }).toList(),
          ),
        ],
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
    final lifecycle = payload['lifecycle_status'] as String? ?? 'approval_pending';
    final isActionable = payload['is_actionable'] as bool? ?? false;

    return _CardShell(
      title: AppStrings.quickNotePreview,
      lifecycle: lifecycle,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Text(payload['normalized_content'] as String? ?? ''),
          const SizedBox(height: 12),
          _SectionChips(title: AppStrings.tagsField, values: tags),
          _SectionList(title: AppStrings.evidenceField, values: evidenceDigest),
          if (isActionable) ...<Widget>[
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
        ],
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

    return _CardShell(
      title: AppStrings.resultCard,
      lifecycle: 'completed',
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Text(summary),
          if (payload['title'] is String) ...<Widget>[
            const SizedBox(height: 8),
            Text('${AppStrings.titleField}：${payload['title']}'),
          ],
          if (payload['start'] is Map<String, dynamic> && payload['end'] is Map<String, dynamic>) ...<Widget>[
            const SizedBox(height: 8),
            Text(
              '${AppStrings.startField}：'
              '${formatEventRange(
                start: EventDateTimeValue.fromJson(payload['start'] as Map<String, dynamic>),
                end: EventDateTimeValue.fromJson(payload['end'] as Map<String, dynamic>),
                isAllDay: false,
              )}',
            ),
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
    );
  }
}


class _CardShell extends StatelessWidget {
  const _CardShell({
    required this.title,
    required this.lifecycle,
    required this.child,
  });

  final String title;
  final String lifecycle;
  final Widget child;

  @override
  Widget build(BuildContext context) {
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            Row(
              children: <Widget>[
                Expanded(
                  child: Text(title, style: Theme.of(context).textTheme.titleMedium),
                ),
                Chip(label: Text(AppStrings.lifecycleLabel(lifecycle))),
              ],
            ),
            const SizedBox(height: 12),
            child,
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
