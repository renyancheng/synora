import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../app_controller.dart';
import '../attachment_picker.dart';
import '../models.dart';
import '../strings.dart';
import 'chat/chat_composer.dart';
import 'chat/chat_message_list.dart';
import 'chat/chat_sidebar.dart';
import 'quick_note_list_page.dart';
import 'schedule_list_page.dart';
import 'settings_page.dart';

/// 聊天主页：只负责布局编排（侧边栏 / 消息列表 / 输入器）与页面级交互
/// （导航、发送、复制、重试、滚动跟随）。
///
/// 组件拆分：
/// - ChatSidebar：侧边栏（抽屉 / 宽屏常驻）
/// - ChatMessageList：消息列表 + 回到底部按钮
/// - ChatComposer：输入器与工具/附件菜单
/// - ChatMessageView / StructuredMessageCard / ReasoningTraceCard：消息气泡、
///   审批卡片与推理轨迹
class ChatHomePage extends StatefulWidget {
  const ChatHomePage({super.key, required this.controller});

  final AppController controller;

  @override
  State<ChatHomePage> createState() => _ChatHomePageState();
}

class _ChatHomePageState extends State<ChatHomePage> {
  late final TextEditingController _textController;
  late final ScrollController _scrollController;
  final GlobalKey<ScaffoldState> _scaffoldKey = GlobalKey<ScaffoldState>();
  int? _boundConversationId;
  String? _lastShownError;
  bool _isSyncingComposer = false;
  /// 是否跟随流式内容滚动（用户靠近底部时自动跟随；主动上滑后暂停跟随）。
  bool _followStream = true;
  /// 用户离开底部后显示“回到底部”按钮。
  bool _showJumpToBottom = false;

  /// 仅当窄屏抽屉处于打开状态时关闭它；宽屏常驻侧边栏下为 no-op。
  void _closeDrawerIfOpen() {
    final scaffold = _scaffoldKey.currentState;
    if (scaffold != null && scaffold.isDrawerOpen) {
      Navigator.of(context).pop();
    }
  }

  @override
  void initState() {
    super.initState();
    _textController = TextEditingController()
      ..addListener(_handleComposerChanged);
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

  /// 是否已接近列表底部（阈值内视为“跟随”）。
  bool _isNearBottom() {
    if (!_scrollController.hasClients) {
      return true;
    }
    final position = _scrollController.position;
    return position.maxScrollExtent - position.pixels < 96;
  }

  /// 仅在用户滚动（拖动/滚轮/键盘）更新期间重新评估跟随状态。
  /// 流式内容增高只产生 ScrollMetricsNotification，不会误判为用户离开底部；
  /// 程序化 jumpTo 虽会产生 ScrollUpdateNotification，但位置始终在底部，
  /// 评估结果不变（跟随 + 无按钮），因此无副作用。
  bool _handleScrollNotification(ScrollNotification notification) {
    if (notification is ScrollUpdateNotification &&
        (notification.dragDetails != null ||
            notification.scrollDelta != null)) {
      final near = _isNearBottom();
      if (near != _followStream || _showJumpToBottom == near) {
        setState(() {
          _followStream = near;
          _showJumpToBottom = !near;
        });
      }
    }
    return false;
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
    if (force ||
        _boundConversationId != activeConversationId ||
        _textController.text != widget.controller.draftText) {
      _boundConversationId = activeConversationId;
      _isSyncingComposer = true;
      _textController.value = TextEditingValue(
        text: widget.controller.draftText,
        selection: TextSelection.collapsed(
          offset: widget.controller.draftText.length,
        ),
      );
      _isSyncingComposer = false;
    }
  }

  Future<void> _openSettings() async {
    _closeDrawerIfOpen();
    await Navigator.of(context).push<void>(
      MaterialPageRoute<void>(
        builder: (_) => SettingsPage(controller: widget.controller),
      ),
    );
  }

  Future<void> _openSchedules() async {
    _closeDrawerIfOpen();
    await Navigator.of(context).push<void>(
      MaterialPageRoute<void>(
        builder: (_) => ScheduleListPage(controller: widget.controller),
      ),
    );
  }

  Future<void> _openQuickNotes() async {
    _closeDrawerIfOpen();
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
    _closeDrawerIfOpen();
    widget.controller.beginDraftConversation();
    _syncComposerFromController(force: true);
    _scrollToBottom();
  }

  Future<void> _selectConversation(int conversationId) async {
    _closeDrawerIfOpen();
    try {
      await widget.controller.selectConversation(conversationId);
      _syncComposerFromController(force: true);
      _scrollToBottom();
    } catch (error) {
      _showMessage(error.toString());
    }
  }

  Future<void> _showConversationMenu(
    BuildContext itemContext,
    ConversationThreadItem item,
  ) async {
    final overlay =
        Overlay.of(itemContext).context.findRenderObject() as RenderBox;
    final button = itemContext.findRenderObject() as RenderBox;
    final result = await showMenu<String>(
      context: context,
      position: RelativeRect.fromRect(
        Rect.fromPoints(
          button.localToGlobal(Offset.zero, ancestor: overlay),
          button.localToGlobal(
            button.size.bottomRight(Offset.zero),
            ancestor: overlay,
          ),
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
              decoration: const InputDecoration(
                hintText: AppStrings.renameConversationHint,
              ),
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
      await widget.controller.renameConversation(
        conversationId: item.id,
        title: result,
      );
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
    final result = await showModalBottomSheet<ComposerMenuResult>(
      context: context,
      showDragHandle: true,
      builder: (context) =>
          ComposerMenu(selectedTool: widget.controller.draftTool),
    );
    if (result == null || !mounted) {
      return;
    }
    if (result.type == ComposerMenuResultType.selectTool) {
      widget.controller.setDraftTool(result.tool);
      return;
    }
    try {
      switch (result.type) {
        case ComposerMenuResultType.gallery:
          final files = await AttachmentPicker.pickGalleryImages();
          if (files.isNotEmpty && mounted) {
            widget.controller.addDraftAttachments(
              files.map(ComposerAttachment.local).toList(),
            );
          }
          break;
        case ComposerMenuResultType.camera:
          final file = await AttachmentPicker.pickPhoto();
          if (file != null && mounted) {
            widget.controller.addDraftAttachments(<ComposerAttachment>[
              ComposerAttachment.local(file),
            ]);
          }
          break;
        case ComposerMenuResultType.file:
          final files = await AttachmentPicker.pickFiles();
          if (files.isNotEmpty && mounted) {
            widget.controller.addDraftAttachments(
              files.map(ComposerAttachment.local).toList(),
            );
          }
          break;
        case ComposerMenuResultType.selectTool:
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

  Future<void> _performAction(
    String action, {
    Map<String, dynamic> payload = const <String, dynamic>{},
  }) async {
    try {
      await widget.controller.performConversationAction(
        action: action,
        payload: payload,
      );
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
  }

  /// 重试失败回答 / 重新生成已完成回答（仅当前会话最后一轮 assistant 文本消息）。
  Future<void> _retryOrRegenerate(ConversationMessageItem message) async {
    try {
      await widget.controller.retryOrRegenerateMessage(message);
      _syncComposerFromController(force: true);
      _scrollToBottom();
    } catch (error) {
      _showMessage(error.toString());
    }
  }

  void _editResendMessage(ConversationMessageItem message) {
    widget.controller
        .editResendMessage(message)
        .then((_) {
          if (!mounted) {
            return;
          }
          _syncComposerFromController(force: true);
          _scrollToBottom();
        })
        .catchError((error) {
          if (!mounted) {
            return;
          }
          _showMessage(error.toString());
        });
  }

  void _showMessage(String message) {
    ScaffoldMessenger.of(
      context,
    ).showSnackBar(SnackBar(content: Text(message)));
  }

  /// 回到列表底部：恢复跟随状态（供用户主动操作与页面级跳转使用）。
  void _scrollToBottom() {
    final changed = !_followStream || _showJumpToBottom;
    _followStream = true;
    _showJumpToBottom = false;
    if (changed && mounted) {
      setState(() {});
    }
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!mounted || !_scrollController.hasClients) {
        return;
      }
      final position = _scrollController.position;
      if (position.maxScrollExtent - position.pixels > 1) {
        _scrollController.jumpTo(position.maxScrollExtent);
      }
    });
  }

  /// 流式内容变化时的跟随：仅在用户仍处于底部附近时把新内容滚进视野，
  /// 用户主动上滑后不再强制拉回底部。
  void _followStreamContent() {
    if (!_followStream || !_scrollController.hasClients) {
      return;
    }
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!mounted || !_followStream || !_scrollController.hasClients) {
        return;
      }
      final position = _scrollController.position;
      if (position.maxScrollExtent - position.pixels > 1) {
        _scrollController.jumpTo(position.maxScrollExtent);
      }
    });
  }

  @override
  Widget build(BuildContext context) {
    return AnimatedBuilder(
      animation: widget.controller,
      builder: (context, _) {
        _syncComposerFromController();
        final user = widget.controller.session?.user;
        final latestError = widget.controller.lastError;
        // 新消息、流式 delta、卡片插入与消息完成都会触发 rebuild：只要用户仍
        // 在底部附近就跟随滚动；用户主动上滑后由 _followStream 门控不再拉回。
        _followStreamContent();
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
        final hasInput =
            _textController.text.trim().isNotEmpty || attachments.isNotEmpty;
        final sending = widget.controller.isMessageSending;
        final composerLocked = sending;
        final statusLabel = widget.controller.streamStatusLabel;

        return LayoutBuilder(
          builder: (context, constraints) {
            // R4：宽屏（>=1000px）侧边栏常驻，窄屏退回抽屉
            final wide = constraints.maxWidth >= 1000;
            final sidebar = _buildSidebar(user: user);
            final chatBody = _buildChatBody(
              sending: sending,
              composerLocked: composerLocked,
              statusLabel: statusLabel,
              selectedTool: selectedTool,
              attachments: attachments,
              hasInput: hasInput,
            );
            return Scaffold(
              key: _scaffoldKey,
              // R4：宽屏侧边栏常驻，无需顶部 AppBar；窄屏抽屉入口仍保留。
              appBar: wide
                  ? null
                  : AppBar(
                      leading: Builder(
                        builder: (context) => IconButton(
                          onPressed: () => Scaffold.of(context).openDrawer(),
                          icon: const Icon(Icons.menu),
                          tooltip: '打开侧边栏',
                        ),
                      ),
                      title: const Text(AppStrings.appTitle),
                    ),
              drawer: wide
                  ? null
                  : Drawer(child: SafeArea(child: sidebar)),
              body: wide
                  ? Row(
                      children: <Widget>[
                        SizedBox(
                          width: 300,
                          child: SafeArea(child: sidebar),
                        ),
                        const VerticalDivider(
                          width: 1,
                          color: Color(0xFFE2ECE8),
                        ),
                        Expanded(child: chatBody),
                      ],
                    )
                  : chatBody,
            );
          },
        );
      },
    );
  }

  /// 侧边栏内容：窄屏抽屉与宽屏常驻栏共用。
  Widget _buildSidebar({required UserProfile? user}) {
    return ChatSidebar(
      controller: widget.controller,
      user: user,
      onOpenSettings: _openSettings,
      onOpenSchedules: _openSchedules,
      onOpenQuickNotes: _openQuickNotes,
      onCreateConversation: _createNewConversation,
      onSelectConversation: _selectConversation,
      onShowConversationMenu: _showConversationMenu,
    );
  }

  /// 聊天区：消息列表 + 输入区（窄屏/宽屏共用）。
  Widget _buildChatBody({
    required bool sending,
    required bool composerLocked,
    required String? statusLabel,
    required ConversationTool? selectedTool,
    required List<ComposerAttachment> attachments,
    required bool hasInput,
  }) {
    return Column(
      children: <Widget>[
        // 无障碍语义播报：liveRegion 仅在状态切换（发送/生成/工具/审批/完成/
        // 取消/失败）时更新标签，不随流式 delta 逐 token 播报。
        Semantics(
          liveRegion: true,
          container: true,
          label: widget.controller.streamAnnouncement ?? '',
          child: const SizedBox.shrink(),
        ),
        Expanded(
          child: ChatMessageList(
            controller: widget.controller,
            scrollController: _scrollController,
            showJumpToBottom: _showJumpToBottom,
            onJumpToBottom: _scrollToBottom,
            onScrollNotification: _handleScrollNotification,
            onAction: _performAction,
            onCopyMessage: _copyMessage,
            onEditResend: _editResendMessage,
            onRetryOrRegenerate: _retryOrRegenerate,
          ),
        ),
        ChatComposer(
          textController: _textController,
          statusLabel: statusLabel,
          selectedTool: selectedTool,
          attachments: attachments,
          composerLocked: composerLocked,
          sending: sending,
          hasInput: hasInput,
          onOpenMenu: _openComposerMenu,
          onSend: _sendMessage,
          onStop: widget.controller.stopCurrentGeneration,
          onRemoveTool: () => widget.controller.setDraftTool(null),
          onRemoveAttachmentAt: widget.controller.removeDraftAttachmentAt,
        ),
      ],
    );
  }
}
