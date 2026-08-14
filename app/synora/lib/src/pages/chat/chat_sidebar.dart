import 'package:flutter/material.dart';

import '../../app_controller.dart';
import '../../models.dart';
import '../../strings.dart';

/// 侧边栏内容：窄屏抽屉与宽屏常驻栏共用。
///
/// 只负责渲染与派发交互回调，业务动作由页面编排层（ChatHomePage）执行。
class ChatSidebar extends StatelessWidget {
  const ChatSidebar({
    super.key,
    required this.controller,
    required this.user,
    required this.onOpenSettings,
    required this.onOpenSchedules,
    required this.onOpenQuickNotes,
    required this.onCreateConversation,
    required this.onSelectConversation,
    required this.onShowConversationMenu,
  });

  final AppController controller;
  final UserProfile? user;
  final VoidCallback onOpenSettings;
  final VoidCallback onOpenSchedules;
  final VoidCallback onOpenQuickNotes;
  final VoidCallback onCreateConversation;
  final ValueChanged<int> onSelectConversation;
  final void Function(BuildContext itemContext, ConversationThreadItem item)
  onShowConversationMenu;

  @override
  Widget build(BuildContext context) {
    return Column(
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
                onPressed: onOpenSettings,
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
          onTap: onOpenSchedules,
        ),
        ListTile(
          leading: const Icon(Icons.sticky_note_2_outlined),
          title: const Text(AppStrings.myQuickNotes),
          onTap: onOpenQuickNotes,
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
                onPressed: controller.isDraftConversation
                    ? null
                    : onCreateConversation,
                icon: const Icon(Icons.add),
                label: const Text(AppStrings.newConversation),
              ),
            ],
          ),
        ),
        Expanded(
          child: controller.conversations.isEmpty
              ? const Center(
                  child: Text(AppStrings.emptyConversationHistory),
                )
              : ListView.builder(
                  itemCount: controller.conversations.length,
                  itemBuilder: (context, index) {
                    final item = controller.conversations[index];
                    final selected =
                        item.id == controller.activeConversationId;
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
                          onPressed: () =>
                              onShowConversationMenu(itemContext, item),
                        ),
                      ),
                      onTap: () => onSelectConversation(item.id),
                    );
                  },
                ),
        ),
      ],
    );
  }
}
