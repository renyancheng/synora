import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../../models.dart';
import '../../strings.dart';

/// 聊天输入区：状态行、工具/附件 chips、输入框与发送/停止按钮。
///
/// 只负责渲染与派发回调，业务动作（发送、停止、选工具、加附件）由页面编排层执行。
class ChatComposer extends StatelessWidget {
  const ChatComposer({
    super.key,
    required this.textController,
    required this.statusLabel,
    required this.selectedTool,
    required this.attachments,
    required this.composerLocked,
    required this.sending,
    required this.hasInput,
    required this.onOpenMenu,
    required this.onSend,
    required this.onStop,
    required this.onRemoveTool,
    required this.onRemoveAttachmentAt,
  });

  final TextEditingController textController;
  final String? statusLabel;
  final ConversationTool? selectedTool;
  final List<ComposerAttachment> attachments;
  final bool composerLocked;
  final bool sending;
  final bool hasInput;
  final VoidCallback onOpenMenu;
  final VoidCallback onSend;
  final VoidCallback onStop;
  final VoidCallback onRemoveTool;
  final ValueChanged<int> onRemoveAttachmentAt;

  @override
  Widget build(BuildContext context) {
    final status = statusLabel;
    final tool = selectedTool;
    return SafeArea(
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
            if (status != null) ...<Widget>[
              Padding(
                padding: const EdgeInsets.only(bottom: 8),
                child: Text(
                  status,
                  style: Theme.of(context).textTheme.bodySmall?.copyWith(
                    color: const Color(0xFF4A6C63),
                  ),
                ),
              ),
            ],
            if (tool != null) ...<Widget>[
              Padding(
                padding: const EdgeInsets.only(bottom: 8),
                child: InputChip(
                  label: Text(
                    '${AppStrings.selectedToolPrefix}：${AppStrings.toolLabel(tool.apiValue)}',
                  ),
                  onDeleted: composerLocked ? null : onRemoveTool,
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
                    onDeleted: composerLocked
                        ? null
                        : () => onRemoveAttachmentAt(entry.key),
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
                    onPressed: composerLocked ? null : onOpenMenu,
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
                      child: Focus(
                        onKeyEvent: (node, event) {
                          // Ctrl/Cmd+Enter 发送，单 Enter 保留为换行
                          if (event is KeyDownEvent &&
                              event.logicalKey == LogicalKeyboardKey.enter &&
                              (HardwareKeyboard.instance.isControlPressed ||
                                  HardwareKeyboard.instance.isMetaPressed)) {
                            onSend();
                            return KeyEventResult.handled;
                          }
                          return KeyEventResult.ignored;
                        },
                        child: TextField(
                          controller: textController,
                          minLines: 1,
                          maxLines: 6,
                          textAlignVertical: TextAlignVertical.center,
                          textInputAction: TextInputAction.newline,
                          decoration: const InputDecoration(
                            hintText: AppStrings.composerHint,
                            isDense: false,
                            contentPadding: EdgeInsets.symmetric(
                              horizontal: 16,
                              vertical: 18,
                            ),
                          ),
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
                      transitionBuilder: (child, animation) =>
                          ScaleTransition(scale: animation, child: child),
                      child: sending
                          ? IconButton.filled(
                              key: const ValueKey('stop'),
                              onPressed: onStop,
                              icon: const Icon(
                                Icons.stop_circle_outlined,
                                size: 24,
                              ),
                              tooltip: AppStrings.stopGenerating,
                              style: IconButton.styleFrom(
                                backgroundColor: const Color(0xFFC25A45),
                                foregroundColor: Colors.white,
                              ),
                            )
                          : hasInput
                          ? IconButton.filled(
                              key: const ValueKey('send'),
                              onPressed: onSend,
                              icon: const Icon(
                                Icons.send_rounded,
                                size: 20,
                              ),
                              tooltip: AppStrings.send,
                            )
                          : IconButton.filled(
                              key: const ValueKey('send-disabled'),
                              onPressed: hasInput ? onSend : null,
                              icon: const Icon(
                                Icons.send_rounded,
                                size: 20,
                              ),
                              tooltip: AppStrings.send,
                            ),
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

enum ComposerMenuResultType { gallery, camera, file, selectTool }

class ComposerMenuResult {
  const ComposerMenuResult({required this.type, this.tool});

  final ComposerMenuResultType type;
  final ConversationTool? tool;
}

class ComposerMenu extends StatelessWidget {
  const ComposerMenu({super.key, required this.selectedTool});

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
                      const ComposerMenuResult(
                        type: ComposerMenuResultType.gallery,
                      ),
                    ),
                  ),
                ),
                const SizedBox(width: 12),
                Expanded(
                  child: _AttachmentActionButton(
                    icon: Icons.photo_camera_outlined,
                    label: AppStrings.attachmentCamera,
                    onTap: () => Navigator.of(context).pop(
                      const ComposerMenuResult(
                        type: ComposerMenuResultType.camera,
                      ),
                    ),
                  ),
                ),
                const SizedBox(width: 12),
                Expanded(
                  child: _AttachmentActionButton(
                    icon: Icons.folder_open_outlined,
                    label: AppStrings.attachmentFile,
                    onTap: () => Navigator.of(context).pop(
                      const ComposerMenuResult(
                        type: ComposerMenuResultType.file,
                      ),
                    ),
                  ),
                ),
              ],
            ),
            const SizedBox(height: 20),
            Text(
              AppStrings.toolsSectionTitle,
              style: Theme.of(context).textTheme.titleSmall,
            ),
            const SizedBox(height: 8),
            _ToolListTile(
              icon: Icons.calendar_month_outlined,
              title: AppStrings.scheduleTool,
              subtitle: AppStrings.scheduleToolDescription,
              selected: selectedTool == ConversationTool.schedule,
              onTap: () => Navigator.of(context).pop(
                const ComposerMenuResult(
                  type: ComposerMenuResultType.selectTool,
                  tool: ConversationTool.schedule,
                ),
              ),
            ),
            const SizedBox(height: 8),
            _ToolListTile(
              icon: Icons.sticky_note_2_outlined,
              title: AppStrings.quickNoteTool,
              subtitle: AppStrings.quickNoteToolDescription,
              selected: selectedTool == ConversationTool.quickNote,
              onTap: () => Navigator.of(context).pop(
                const ComposerMenuResult(
                  type: ComposerMenuResultType.selectTool,
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
                    Text(
                      subtitle,
                      style: Theme.of(context).textTheme.bodySmall,
                    ),
                  ],
                ),
              ),
              if (selected)
                const Icon(Icons.check_circle, color: Color(0xFF176B5A)),
            ],
          ),
        ),
      ),
    );
  }
}
