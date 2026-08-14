import 'package:flutter/material.dart';

import '../../app_controller.dart';
import '../../models.dart';
import '../../strings.dart';
import 'chat_message_view.dart';
import 'reasoning_trace_card.dart';

/// 聊天消息列表：空态 / 加载态 / 消息滚动列表 + “回到底部”按钮。
///
/// 滚动跟随状态与跳转行为由页面编排层控制（scrollController、通知回调、
/// 按钮可见性与 onJumpToBottom），本组件只负责渲染与派发消息级回调。
class ChatMessageList extends StatelessWidget {
  const ChatMessageList({
    super.key,
    required this.controller,
    required this.scrollController,
    required this.showJumpToBottom,
    required this.onJumpToBottom,
    required this.onScrollNotification,
    required this.onAction,
    required this.onCopyMessage,
    required this.onEditResend,
    required this.onRetryOrRegenerate,
  });

  final AppController controller;
  final ScrollController scrollController;
  final bool showJumpToBottom;
  final VoidCallback onJumpToBottom;
  final bool Function(ScrollNotification notification) onScrollNotification;
  final Future<void> Function(String action, {Map<String, dynamic> payload})
  onAction;
  final void Function(ConversationMessageItem message) onCopyMessage;
  final void Function(ConversationMessageItem message) onEditResend;
  final void Function(ConversationMessageItem message) onRetryOrRegenerate;

  /// 实时推理步骤中"规划类"的最新一步（plan / perceive），其余步骤不参与展示。
  static ReasoningStepItem? _latestPlanningStep(List<ReasoningStepItem> steps) {
    for (final step in steps.reversed) {
      if (step.stepType == 'plan' || step.stepType == 'perceive') {
        return step;
      }
    }
    return null;
  }

  /// 是否为最新一条“可见”的 assistant 文本消息。
  /// reasoning_step 卡片不渲染任何 UI，不应让它抢占文本消息上方的 plan 横幅；
  /// 其余可见卡片（日程/速记）仍按原逻辑让横幅让位。
  static bool _isLatestVisibleAssistant(
    List<ConversationMessageItem> messages,
    int index,
    ConversationMessageItem message,
  ) {
    if (message.isUser || message.messageType != 'text') {
      return false;
    }
    for (var i = index + 1; i < messages.length; i++) {
      if (messages[i].messageType != 'reasoning_step') {
        return false;
      }
    }
    return true;
  }

  @override
  Widget build(BuildContext context) {
    final messages = controller.messages;
    final liveSteps = controller.liveReasoningStepsFor(
      controller.activeConversationId,
    );
    return Stack(
      children: <Widget>[
        Positioned.fill(
          child: controller.isConversationLoading && messages.isEmpty
              ? const Center(child: CircularProgressIndicator())
              : messages.isEmpty
              ? const Center(child: Text(AppStrings.emptyConversation))
              : NotificationListener<ScrollNotification>(
                  onNotification: onScrollNotification,
                  child: ListView.builder(
                    controller: scrollController,
                    padding: const EdgeInsets.fromLTRB(16, 16, 16, 8),
                    itemCount: messages.length,
                    itemBuilder: (context, index) {
                      final message = messages[index];
                      final isLatestAssistant = _isLatestVisibleAssistant(
                        messages,
                        index,
                        message,
                      );
                      final messageView = ChatMessageView(
                        message: message,
                        onAction: onAction,
                        onCopy: (message.textContent ?? '').trim().isNotEmpty
                            ? () => onCopyMessage(message)
                            : null,
                        onEditResend: controller.canEditMessage(message)
                            ? () => onEditResend(message)
                            : null,
                        onRetry:
                            message.status == 'failed' &&
                                controller.canRetryOrRegenerate(message)
                            ? () => onRetryOrRegenerate(message)
                            : null,
                        onRegenerate:
                            message.status == 'completed' &&
                                controller.canRetryOrRegenerate(message)
                            ? () => onRetryOrRegenerate(message)
                            : null,
                      );
                      if (!isLatestAssistant) {
                        return messageView;
                      }
                      // 实时状态槽位（气泡外独立展示）：
                      // 用户发送后 AI 未返回 → 只显示呼吸点；
                      // plan 到达 → 显示 plan 行并关闭呼吸点；
                      // act 产出文本 → 气泡内显示内容；
                      // 完成后 → 静态保留最后一条 plan 行。
                      Widget? statusWidget;
                      if (message.status == 'streaming') {
                        final livePlan = _latestPlanningStep(liveSteps);
                        if (livePlan != null) {
                          statusWidget = ReasoningBanner(
                            text: livePlan.content.trim().isEmpty
                                ? '正在思考…'
                                : livePlan.content.trim(),
                            flowing: true,
                          );
                        } else if ((message.textContent ?? '').trim().isEmpty) {
                          statusWidget = const Align(
                            alignment: Alignment.centerLeft,
                            child: Padding(
                              padding: EdgeInsets.only(bottom: 6, left: 4),
                              child: BreathingDot(size: 12),
                            ),
                          );
                        }
                      } else {
                        final finalPlan = controller.finalPlanTextFor(
                          controller.activeConversationId,
                        );
                        if (finalPlan != null && finalPlan.trim().isNotEmpty) {
                          statusWidget = ReasoningBanner(
                            text: finalPlan,
                            flowing: false,
                          );
                        }
                      }
                      if (statusWidget == null) {
                        return messageView;
                      }
                      return Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: <Widget>[
                          statusWidget,
                          const SizedBox(height: 4),
                          messageView,
                        ],
                      );
                    },
                  ),
                ),
        ),
        // 用户主动上滑离开底部后显示；点击恢复跟随并滚回底部。
        Positioned(
          right: 20,
          bottom: 16,
          child: showJumpToBottom
              ? FloatingActionButton.small(
                  key: const ValueKey<String>('jump-to-bottom'),
                  heroTag: 'jump-to-bottom',
                  tooltip: AppStrings.jumpToBottom,
                  onPressed: onJumpToBottom,
                  child: const Icon(Icons.arrow_downward_rounded),
                )
              : const SizedBox.shrink(),
        ),
      ],
    );
  }
}
