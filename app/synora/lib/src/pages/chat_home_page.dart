import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../app_controller.dart';
import '../attachment_picker.dart';
import '../date_utils.dart';
import '../models.dart';
import '../strings.dart';
import '../voice_input_service.dart';
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
  int? _boundConversationId;
  int _lastMessageCount = 0;
  String? _lastShownError;
  bool _isSyncingComposer = false;
  final VoiceInputService _voiceInputService = VoiceInputService();
  VoiceInputState _voiceState = VoiceInputState.idle;
  double? _voiceDownloadProgress;

  bool get _isVoiceBusy =>
      _voiceState == VoiceInputState.downloading ||
      _voiceState == VoiceInputState.initializing ||
      _voiceState == VoiceInputState.listening ||
      _voiceState == VoiceInputState.processing;

  bool get _isVoiceListening => _voiceState == VoiceInputState.listening;

  String? get _voiceStatusLabel {
    switch (_voiceState) {
      case VoiceInputState.downloading:
        if (_voiceDownloadProgress != null) {
          final percent = (_voiceDownloadProgress! * 100).clamp(0, 100).toStringAsFixed(0);
          return '${AppStrings.voiceDownloading} $percent%';
        }
        return AppStrings.voiceDownloading;
      case VoiceInputState.initializing:
        return AppStrings.voiceInitializing;
      case VoiceInputState.listening:
        return AppStrings.voiceListening;
      case VoiceInputState.processing:
        return AppStrings.voiceProcessing;
      case VoiceInputState.failed:
      case VoiceInputState.idle:
        return null;
    }
  }

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
    _voiceInputService.dispose();
    super.dispose();
  }

  Future<void> _bootstrap() async {
    if (!widget.controller.isAuthenticated) {
      return;
    }
    try {
      await widget.controller.ensureConversationReady();
      _syncComposerFromController(force: true);
    } catch (error) {
      _showMessage(error.toString());
    }
  }

  void _handleComposerChanged() {
    if (_isSyncingComposer) {
      return;
    }
    final value = _textController.text;
    if (value != widget.controller.draftText) {
      widget.controller.updateDraftText(value);
    }
  }

  void _syncComposerFromController({bool force = false}) {
    final activeConversationId = widget.controller.activeConversationId;
    if (force || _boundConversationId != activeConversationId || _textController.text != widget.controller.draftText) {
      _boundConversationId = activeConversationId;
      _isSyncingComposer = true;
      _textController.value = TextEditingValue(
        text: widget.controller.draftText,
        selection: TextSelection.collapsed(offset: widget.controller.draftText.length),
      );
      _isSyncingComposer = false;
    }
  }

  Future<void> _openSettings() async {
    if (Navigator.of(context).canPop()) {
      Navigator.of(context).pop();
    }
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
    if (widget.controller.isDraftConversation) {
      return;
    }
    Navigator.of(context).pop();
    widget.controller.beginDraftConversation();
    _syncComposerFromController(force: true);
    _scrollToBottom();
  }

  Future<void> _selectConversation(int conversationId) async {
    Navigator.of(context).pop();
    try {
      await widget.controller.selectConversation(conversationId);
      _syncComposerFromController(force: true);
      _scrollToBottom();
    } catch (error) {
      _showMessage(error.toString());
    }
  }

  Future<void> _showConversationMenu(BuildContext itemContext, ConversationThreadItem item) async {
    final overlay = Overlay.of(itemContext).context.findRenderObject() as RenderBox;
    final button = itemContext.findRenderObject() as RenderBox;
    final result = await showMenu<String>(
      context: context,
      position: RelativeRect.fromRect(
        Rect.fromPoints(
          button.localToGlobal(Offset.zero, ancestor: overlay),
          button.localToGlobal(button.size.bottomRight(Offset.zero), ancestor: overlay),
        ),
        Offset.zero & overlay.size,
      ),
      items: const <PopupMenuEntry<String>>[
        PopupMenuItem<String>(
          value: 'rename',
          child: ListTile(
            contentPadding: EdgeInsets.zero,
            leading: Icon(Icons.edit_outlined),
            title: Text(AppStrings.renameConversation),
          ),
        ),
        PopupMenuItem<String>(
          value: 'delete',
          child: ListTile(
            contentPadding: EdgeInsets.zero,
            leading: Icon(Icons.delete_outline),
            title: Text(AppStrings.deleteConversation),
          ),
        ),
      ],
    );
    if (!mounted || result == null) {
      return;
    }
    if (result == 'rename') {
      await _renameConversation(item);
      return;
    }
    if (result == 'delete') {
      await _deleteConversation(item);
    }
  }

  Future<void> _renameConversation(ConversationThreadItem item) async {
    final controller = TextEditingController(text: item.title);
    final result = await showDialog<String>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text(AppStrings.renameConversation),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            Text(item.title, style: Theme.of(context).textTheme.bodySmall),
            const SizedBox(height: 12),
            TextField(
              controller: controller,
              autofocus: true,
              decoration: const InputDecoration(hintText: AppStrings.renameConversationHint),
            ),
          ],
        ),
        actions: <Widget>[
          TextButton(
            onPressed: () => Navigator.of(context).pop(),
            child: const Text(AppStrings.cancel),
          ),
          FilledButton(
            onPressed: () => Navigator.of(context).pop(controller.text),
            child: const Text(AppStrings.confirmSave),
          ),
        ],
      ),
    );
    if (result == null) {
      return;
    }
    try {
      await widget.controller.renameConversation(conversationId: item.id, title: result);
    } catch (error) {
      _showMessage(error.toString());
    }
  }

  Future<void> _deleteConversation(ConversationThreadItem item) async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text(AppStrings.deleteConversation),
        content: Text(AppStrings.deleteConversationMessage(item.title)),
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
    try {
      await widget.controller.deleteConversation(item.id);
      _syncComposerFromController(force: true);
    } catch (error) {
      _showMessage(error.toString());
    }
  }

  Future<void> _openComposerMenu() async {
    final result = await showModalBottomSheet<_ComposerMenuResult>(
      context: context,
      showDragHandle: true,
      builder: (context) => _ComposerMenu(selectedTool: widget.controller.draftTool),
    );
    if (result == null || !mounted) {
      return;
    }
    if (result.type == _ComposerMenuResultType.selectTool) {
      widget.controller.setDraftTool(result.tool);
      return;
    }
    try {
      switch (result.type) {
        case _ComposerMenuResultType.gallery:
          final files = await AttachmentPicker.pickGalleryImages();
          if (files.isNotEmpty && mounted) {
            widget.controller.addDraftAttachments(files.map(ComposerAttachment.local).toList());
          }
          break;
        case _ComposerMenuResultType.camera:
          final file = await AttachmentPicker.pickPhoto();
          if (file != null && mounted) {
            widget.controller.addDraftAttachments(<ComposerAttachment>[ComposerAttachment.local(file)]);
          }
          break;
        case _ComposerMenuResultType.file:
          final files = await AttachmentPicker.pickFiles();
          if (files.isNotEmpty && mounted) {
            widget.controller.addDraftAttachments(files.map(ComposerAttachment.local).toList());
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
    try {
      await widget.controller.sendChatMessage();
      _syncComposerFromController(force: true);
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

  Future<void> _copyMessage(ConversationMessageItem message) async {
    final text = (message.textContent ?? '').trim();
    if (text.isEmpty) {
      _showMessage(AppStrings.nothingToCopy);
      return;
    }
    await Clipboard.setData(ClipboardData(text: text));
    if (mounted) {
      _showMessage(AppStrings.copied);
    }
  }

  void _editResendMessage(ConversationMessageItem message) {
    widget.controller.editResendMessage(message).then((_) {
      if (!mounted) {
        return;
      }
      _syncComposerFromController(force: true);
      _showMessage(AppStrings.copiedToComposer);
      _scrollToBottom();
    }).catchError((error) {
      if (!mounted) {
        return;
      }
      _showMessage(error.toString());
    });
  }

  Future<void> _toggleVoiceInput() async {
    if (!await _voiceInputService.isSupported) {
      _showMessage(AppStrings.voiceComingSoon);
      return;
    }
    if (widget.controller.isMessageSending) {
      return;
    }
    if (_isVoiceListening) {
      await _stopVoiceInput();
      return;
    }
    if (_isVoiceBusy) {
      return;
    }
    await _startVoiceInput();
  }

  Future<void> _startVoiceInput() async {
    try {
      _setVoiceState(VoiceInputState.downloading, progress: 0);
      await _voiceInputService.ensureReady(
        onDownloadProgress: (value) => _setVoiceState(VoiceInputState.downloading, progress: value),
      );
      _setVoiceState(VoiceInputState.initializing, clearProgress: true);
      await _voiceInputService.startListening();
      _setVoiceState(VoiceInputState.listening, clearProgress: true);
    } catch (error) {
      _setVoiceFailure(error);
    }
  }

  Future<void> _stopVoiceInput() async {
    try {
      _setVoiceState(VoiceInputState.processing, clearProgress: true);
      final result = await _voiceInputService.stopListening();
      final mergedText = _mergeVoiceText(widget.controller.draftText, result.text);
      widget.controller.updateDraftText(mergedText);
      _syncComposerFromController(force: true);
      _setVoiceState(VoiceInputState.idle, clearProgress: true);
    } catch (error) {
      _setVoiceFailure(error);
    }
  }

  String _mergeVoiceText(String current, String incoming) {
    final base = current.trimRight();
    final addition = incoming.trim();
    if (addition.isEmpty) {
      return current;
    }
    if (base.isEmpty) {
      return addition;
    }
    return '$base\n$addition';
  }

  void _setVoiceFailure(Object error) {
    final message = error is VoiceInputException
        ? AppStrings.voiceErrorReason(error.code, error.message)
        : AppStrings.voiceErrorReason(null, error.toString());
    _setVoiceState(VoiceInputState.failed, clearProgress: true);
    _showMessage(message);
    _setVoiceState(VoiceInputState.idle, clearProgress: true);
  }

  void _setVoiceState(
    VoiceInputState state, {
    double? progress,
    bool clearProgress = false,
  }) {
    if (!mounted) {
      return;
    }
    setState(() {
      _voiceState = state;
      _voiceDownloadProgress = clearProgress ? null : (progress ?? _voiceDownloadProgress);
    });
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
        _syncComposerFromController();
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

        final attachments = widget.controller.draftAttachments;
        final selectedTool = widget.controller.draftTool;
        final hasInput = _textController.text.trim().isNotEmpty || attachments.isNotEmpty;
        final sending = widget.controller.isMessageSending;
        final voiceBusy = _isVoiceBusy;
        final voiceListening = _isVoiceListening;
        final composerLocked = sending || voiceBusy;
        final statusLabel = _voiceStatusLabel ?? widget.controller.streamStatusLabel;

        return Scaffold(
          appBar: AppBar(
            leading: Builder(
              builder: (context) => IconButton(
                onPressed: () => Scaffold.of(context).openDrawer(),
                icon: const Icon(Icons.menu),
                tooltip: '打开侧边栏',
              ),
            ),
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
                    leading: const Icon(Icons.calendar_month_outlined),
                    title: const Text(AppStrings.mySchedules),
                    onTap: _openSchedules,
                  ),
                  ListTile(
                    leading: const Icon(Icons.sticky_note_2_outlined),
                    title: const Text(AppStrings.myQuickNotes),
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
                          onPressed: widget.controller.isDraftConversation ? null : _createNewConversation,
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
                                trailing: Builder(
                                  builder: (itemContext) => IconButton(
                                    icon: const Icon(Icons.more_horiz),
                                    tooltip: AppStrings.conversationMenu,
                                    onPressed: () => _showConversationMenu(itemContext, item),
                                  ),
                                ),
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
                                onCopy: message.isUser ? () => _copyMessage(message) : null,
                                onEditResend: widget.controller.canEditMessage(message) ? () => _editResendMessage(message) : null,
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
                      if (statusLabel != null) ...<Widget>[
                        Padding(
                          padding: const EdgeInsets.only(bottom: 8),
                          child: Text(
                            statusLabel,
                            style: Theme.of(context).textTheme.bodySmall?.copyWith(
                                  color: const Color(0xFF4A6C63),
                                ),
                          ),
                        ),
                      ],
                      if (selectedTool != null) ...<Widget>[
                        Padding(
                          padding: const EdgeInsets.only(bottom: 8),
                          child: InputChip(
                            label: Text('${AppStrings.selectedToolPrefix}：${AppStrings.toolLabel(selectedTool.apiValue)}'),
                            onDeleted: composerLocked ? null : () => widget.controller.setDraftTool(null),
                          ),
                        ),
                      ],
                      if (attachments.isNotEmpty) ...<Widget>[
                        Wrap(
                          spacing: 8,
                          runSpacing: 8,
                          children: attachments.asMap().entries.map((entry) {
                            return InputChip(
                              label: Text(entry.value.fileName),
                              onDeleted: composerLocked ? null : () => widget.controller.removeDraftAttachmentAt(entry.key),
                            );
                          }).toList(),
                        ),
                        const SizedBox(height: 10),
                      ],
                      Row(
                        crossAxisAlignment: CrossAxisAlignment.center,
                        children: <Widget>[
                          SizedBox(
                            width: 48,
                            height: 48,
                            child: IconButton(
                              onPressed: composerLocked ? null : _openComposerMenu,
                              tooltip: AppStrings.attach,
                              icon: const Icon(Icons.add_circle_outline),
                            ),
                          ),
                          const SizedBox(width: 8),
                          Expanded(
                            child: ConstrainedBox(
                              constraints: const BoxConstraints(minHeight: 76),
                              child: Align(
                                alignment: Alignment.center,
                                child: TextField(
                                  controller: _textController,
                                  minLines: 1,
                                  maxLines: 6,
                                  readOnly: voiceBusy,
                                  textAlignVertical: TextAlignVertical.center,
                                  textInputAction: TextInputAction.newline,
                                  decoration: const InputDecoration(
                                    hintText: AppStrings.composerHint,
                                    isDense: false,
                                    contentPadding: EdgeInsets.symmetric(horizontal: 16, vertical: 18),
                                  ),
                                ),
                              ),
                            ),
                          ),
                          const SizedBox(width: 8),
                          SizedBox(
                            width: 48,
                            height: 48,
                            child: Center(
                              child: AnimatedSwitcher(
                                duration: const Duration(milliseconds: 220),
                                transitionBuilder: (child, animation) => ScaleTransition(scale: animation, child: child),
                                child: sending
                                    ? const SizedBox(
                                        key: ValueKey('loading'),
                                        width: 48,
                                        height: 48,
                                        child: Center(
                                          child: SizedBox(
                                            width: 20,
                                            height: 20,
                                            child: CircularProgressIndicator(strokeWidth: 2.2),
                                          ),
                                        ),
                                      )
                                    : hasInput
                                        ? IconButton.filled(
                                            key: const ValueKey('send'),
                                            onPressed: _sendMessage,
                                            icon: const Icon(Icons.send_rounded, size: 20),
                                            tooltip: AppStrings.send,
                                          )
                                        : IconButton(
                                            key: ValueKey(voiceListening ? 'voice-stop' : 'voice-start'),
                                            onPressed: _toggleVoiceInput,
                                            icon: Icon(voiceListening ? Icons.stop_rounded : Icons.mic_none),
                                            tooltip: voiceListening ? AppStrings.voiceStop : AppStrings.voiceStart,
                                          ),
                              ),
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
            const SizedBox(height: 20),
            Text(AppStrings.toolsSectionTitle, style: Theme.of(context).textTheme.titleSmall),
            const SizedBox(height: 8),
            _ToolListTile(
              icon: Icons.calendar_month_outlined,
              title: AppStrings.scheduleTool,
              subtitle: AppStrings.scheduleToolDescription,
              selected: selectedTool == ConversationTool.schedule,
              onTap: () => Navigator.of(context).pop(
                const _ComposerMenuResult(type: _ComposerMenuResultType.selectTool, tool: ConversationTool.schedule),
              ),
            ),
            const SizedBox(height: 8),
            _ToolListTile(
              icon: Icons.sticky_note_2_outlined,
              title: AppStrings.quickNoteTool,
              subtitle: AppStrings.quickNoteToolDescription,
              selected: selectedTool == ConversationTool.quickNote,
              onTap: () => Navigator.of(context).pop(
                const _ComposerMenuResult(type: _ComposerMenuResultType.selectTool, tool: ConversationTool.quickNote),
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
    return Material(
      color: const Color(0xFFF4F8F7),
      borderRadius: BorderRadius.circular(20),
      child: InkWell(
        onTap: onTap,
        borderRadius: BorderRadius.circular(20),
        child: Padding(
          padding: const EdgeInsets.symmetric(vertical: 18, horizontal: 8),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: <Widget>[
              Icon(icon, size: 28, color: const Color(0xFF176B5A)),
              const SizedBox(height: 10),
              Text(label),
            ],
          ),
        ),
      ),
    );
  }
}

class _ToolListTile extends StatelessWidget {
  const _ToolListTile({
    required this.icon,
    required this.title,
    required this.subtitle,
    required this.selected,
    required this.onTap,
  });

  final IconData icon;
  final String title;
  final String subtitle;
  final bool selected;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return Material(
      color: selected ? const Color(0xFFEAF5F1) : const Color(0xFFF8FBFA),
      borderRadius: BorderRadius.circular(18),
      child: InkWell(
        onTap: onTap,
        borderRadius: BorderRadius.circular(18),
        child: Padding(
          padding: const EdgeInsets.all(14),
          child: Row(
            children: <Widget>[
              Icon(icon, color: const Color(0xFF176B5A)),
              const SizedBox(width: 12),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: <Widget>[
                    Text(title, style: Theme.of(context).textTheme.titleSmall),
                    const SizedBox(height: 4),
                    Text(subtitle, style: Theme.of(context).textTheme.bodySmall),
                  ],
                ),
              ),
              if (selected) const Icon(Icons.check_circle, color: Color(0xFF176B5A)),
            ],
          ),
        ),
      ),
    );
  }
}

class _ConversationMessageView extends StatelessWidget {
  const _ConversationMessageView({
    required this.message,
    required this.onAction,
    this.onCopy,
    this.onEditResend,
  });

  final ConversationMessageItem message;
  final Future<void> Function(String action, {Map<String, dynamic> payload}) onAction;
  final VoidCallback? onCopy;
  final VoidCallback? onEditResend;

  @override
  Widget build(BuildContext context) {
    final isUser = message.isUser;
    if (message.messageType == 'text') {
      return Align(
        alignment: isUser ? Alignment.centerRight : Alignment.centerLeft,
        child: Column(
          crossAxisAlignment: isUser ? CrossAxisAlignment.end : CrossAxisAlignment.start,
          children: <Widget>[
            Container(
              constraints: const BoxConstraints(maxWidth: 580),
              margin: const EdgeInsets.only(bottom: 6),
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
                  if ((message.textContent ?? '').trim().isNotEmpty)
                    Text(
                      message.textContent ?? '',
                      style: TextStyle(color: isUser ? Colors.white : const Color(0xFF173C35), height: 1.55),
                    ),
                  if (message.isUser && (message.attachmentRefs.isNotEmpty || message.localAttachments.isNotEmpty || message.selectedTool != null)) ...<Widget>[
                    const SizedBox(height: 10),
                    Wrap(
                      spacing: 8,
                      runSpacing: 8,
                      children: <Widget>[
                        ...message.attachmentRefs.map((item) => _MetaChip(label: item.fileName, icon: Icons.attach_file, dark: isUser)),
                        ...message.localAttachments.map((item) => _MetaChip(label: item.fileName, icon: Icons.attach_file, dark: isUser)),
                        if (message.selectedTool != null)
                          _MetaChip(
                            label: AppStrings.toolLabel(message.selectedTool!.apiValue),
                            icon: message.selectedTool == ConversationTool.schedule ? Icons.calendar_month_outlined : Icons.sticky_note_2_outlined,
                            dark: isUser,
                          ),
                      ],
                    ),
                  ],
                  if (_statusLabel(message.status) != null) ...<Widget>[
                    const SizedBox(height: 8),
                    Text(
                      _statusLabel(message.status)!,
                      style: TextStyle(
                        color: isUser ? Colors.white70 : const Color(0xFF617B74),
                        fontSize: 12,
                      ),
                    ),
                  ],
                ],
              ),
            ),
            if (message.isUser && (onCopy != null || onEditResend != null))
              Padding(
                padding: const EdgeInsets.only(bottom: 12, right: 4),
                child: Row(
                  mainAxisSize: MainAxisSize.min,
                  children: <Widget>[
                    if (onCopy != null)
                      IconButton(
                        visualDensity: VisualDensity.compact,
                        iconSize: 18,
                        tooltip: AppStrings.copy,
                        onPressed: onCopy,
                        icon: const Icon(Icons.content_copy_outlined),
                      ),
                    if (onEditResend != null)
                      IconButton(
                        visualDensity: VisualDensity.compact,
                        iconSize: 18,
                        tooltip: AppStrings.editResend,
                        onPressed: onEditResend,
                        icon: const Icon(Icons.edit_outlined),
                      ),
                  ],
                ),
              ),
          ],
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

  String? _statusLabel(String status) {
    switch (status) {
      case 'sending':
        return AppStrings.sending;
      case 'streaming':
        return AppStrings.streaming;
      case 'failed':
        return AppStrings.sendFailed;
      default:
        return null;
    }
  }
}

class _MetaChip extends StatelessWidget {
  const _MetaChip({
    required this.label,
    required this.icon,
    required this.dark,
  });

  final String label;
  final IconData icon;
  final bool dark;

  @override
  Widget build(BuildContext context) {
    final backgroundColor = dark ? const Color(0x1FFFFFFF) : const Color(0xFFF0F5F3);
    final foregroundColor = dark ? Colors.white : const Color(0xFF275C52);
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
      decoration: BoxDecoration(
        color: backgroundColor,
        borderRadius: BorderRadius.circular(999),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: <Widget>[
          Icon(icon, size: 14, color: foregroundColor),
          const SizedBox(width: 6),
          Text(
            label,
            style: TextStyle(color: foregroundColor, fontSize: 12),
          ),
        ],
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
      const SnackBar(content: Text(AppStrings.timeFormatHint)),
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
                    child: Text(_busy ? AppStrings.loading : (isEditing ? AppStrings.submitMissingFields : AppStrings.confirmSave)),
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
              '${AppStrings.startField}：${formatEventRange(
                start: EventDateTimeValue.fromJson(payload['start'] as Map<String, dynamic>),
                end: EventDateTimeValue.fromJson(payload['end'] as Map<String, dynamic>),
                isAllDay: false,
              )}',
            ),
          ],
          if (payload['source_text'] is String && (payload['source_text'] as String).trim().isNotEmpty) ...<Widget>[
            const SizedBox(height: 8),
            Text('${AppStrings.sourceTextField}：${payload['source_text']}'),
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
