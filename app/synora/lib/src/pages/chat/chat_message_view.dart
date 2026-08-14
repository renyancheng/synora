import 'package:flutter/material.dart';
import 'package:flutter_markdown/flutter_markdown.dart';

import '../../models.dart';
import '../../strings.dart';
import 'approval_cards.dart';

/// 单条消息视图：用户/assistant 文本气泡、操作行（复制/编辑重发/重试/重新生成）
/// 与结构化卡片分发。
class ChatMessageView extends StatelessWidget {
  const ChatMessageView({
    super.key,
    required this.message,
    required this.onAction,
    this.onCopy,
    this.onEditResend,
    this.onRetry,
    this.onRegenerate,
  });

  final ConversationMessageItem message;
  final Future<void> Function(String action, {Map<String, dynamic> payload})
  onAction;
  final VoidCallback? onCopy;
  final VoidCallback? onEditResend;
  final VoidCallback? onRetry;
  final VoidCallback? onRegenerate;

  @override
  Widget build(BuildContext context) {
    final isUser = message.isUser;
    if (message.messageType == 'text') {
      // 用户发送后 AI 未返回（无文本）：气泡整体隐藏，由列表层在气泡外独立渲染
      // 呼吸点；首个 message_delta 到达后气泡才出现。
      final showBreathing =
          !isUser &&
          message.status == 'streaming' &&
          (message.textContent ?? '').trim().isEmpty;
      if (showBreathing) {
        return const SizedBox.shrink();
      }
      final bubble = Container(
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
              if (isUser)
                SelectableText(
                  message.textContent ?? '',
                  style: const TextStyle(
                    color: Colors.white,
                    height: 1.55,
                  ),
                )
              else
                MarkdownBody(
                  data: message.textContent ?? '',
                  selectable: true,
                  styleSheet: MarkdownStyleSheet.fromTheme(
                    Theme.of(context),
                  ).copyWith(
                    p: const TextStyle(
                      color: Color(0xFF173C35),
                      height: 1.55,
                    ),
                    listBullet: const TextStyle(
                      color: Color(0xFF173C35),
                    ),
                    code: const TextStyle(
                      color: Color(0xFF173C35),
                      fontFamily: 'monospace',
                    ),
                    codeblockDecoration: BoxDecoration(
                      color: const Color(0xFFF3F6F5),
                      borderRadius: BorderRadius.circular(12),
                    ),
                  ),
                ),
            if (message.isUser &&
                (message.attachmentRefs.isNotEmpty ||
                    message.localAttachments.isNotEmpty ||
                    message.selectedTool != null)) ...<Widget>[
              const SizedBox(height: 10),
              Wrap(
                spacing: 8,
                runSpacing: 8,
                children: <Widget>[
                  ...message.attachmentRefs.map(
                    (item) => MetaChip(
                      label: item.fileName,
                      icon: Icons.attach_file,
                      dark: isUser,
                    ),
                  ),
                  ...message.localAttachments.map(
                    (item) => MetaChip(
                      label: item.fileName,
                      icon: Icons.attach_file,
                      dark: isUser,
                    ),
                  ),
                  if (message.selectedTool != null)
                    MetaChip(
                      label: AppStrings.toolLabel(
                        message.selectedTool!.apiValue,
                      ),
                      icon: message.selectedTool == ConversationTool.schedule
                          ? Icons.calendar_month_outlined
                          : Icons.sticky_note_2_outlined,
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
      );
      return Align(
        alignment: isUser ? Alignment.centerRight : Alignment.centerLeft,
        child: Column(
          crossAxisAlignment: isUser
              ? CrossAxisAlignment.end
              : CrossAxisAlignment.start,
          children: <Widget>[
            // assistant 气泡首次出现时播放一次性入场动画（首个 delta 到达后）。
            if (isUser)
              bubble
            else
              AssistantBubbleEntrance(
                key: ValueKey<String>(
                  'assistant-bubble-entrance-${message.id}',
                ),
                child: bubble,
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
            // assistant 文本消息操作：复制 / 重试（失败）/ 重新生成（已完成）。
            // 生成中（streaming/sending）不提供任何操作。
            if (!isUser &&
                (onCopy != null || onRetry != null || onRegenerate != null))
              Padding(
                padding: const EdgeInsets.only(bottom: 12, left: 4),
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
                    if (onRetry != null)
                      IconButton(
                        visualDensity: VisualDensity.compact,
                        iconSize: 18,
                        tooltip: AppStrings.retry,
                        onPressed: onRetry,
                        icon: const Icon(Icons.refresh_rounded),
                      ),
                    if (onRegenerate != null)
                      IconButton(
                        visualDensity: VisualDensity.compact,
                        iconSize: 18,
                        tooltip: AppStrings.regenerate,
                        onPressed: onRegenerate,
                        icon: const Icon(Icons.autorenew_rounded),
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
        child: StructuredMessageCard(message: message, onAction: onAction),
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

class MetaChip extends StatelessWidget {
  const MetaChip({
    super.key,
    required this.label,
    required this.icon,
    required this.dark,
  });

  final String label;
  final IconData icon;
  final bool dark;

  @override
  Widget build(BuildContext context) {
    final backgroundColor = dark
        ? const Color(0x1FFFFFFF)
        : const Color(0xFFF0F5F3);
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
          Text(label, style: TextStyle(color: foregroundColor, fontSize: 12)),
        ],
      ),
    );
  }
}

/// assistant 文本气泡的一次性入场动画：淡入 + 轻微上滑 + 缩放。
/// 首个 message_delta 使气泡进入树时播放一次；后续增量重建不会重播。
/// 系统开启“减少动态效果”时直接静态展示。
class AssistantBubbleEntrance extends StatefulWidget {
  const AssistantBubbleEntrance({super.key, required this.child});

  final Widget child;

  @override
  State<AssistantBubbleEntrance> createState() =>
      _AssistantBubbleEntranceState();
}

class _AssistantBubbleEntranceState extends State<AssistantBubbleEntrance>
    with SingleTickerProviderStateMixin {
  late final AnimationController _controller;
  late final CurvedAnimation _curve;

  @override
  void initState() {
    super.initState();
    _controller = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 280),
    )..forward();
    _curve = CurvedAnimation(parent: _controller, curve: Curves.easeOutCubic);
  }

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    if (MediaQuery.disableAnimationsOf(context)) {
      _controller.value = 1.0;
      _controller.stop();
    }
  }

  @override
  void dispose() {
    _curve.dispose();
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    if (MediaQuery.disableAnimationsOf(context)) {
      return widget.child;
    }
    return FadeTransition(
      opacity: _curve,
      child: SlideTransition(
        position: Tween<Offset>(
          begin: const Offset(0, 0.06),
          end: Offset.zero,
        ).animate(_curve),
        child: ScaleTransition(
          scale: Tween<double>(begin: 0.97, end: 1).animate(_curve),
          child: widget.child,
        ),
      ),
    );
  }
}
