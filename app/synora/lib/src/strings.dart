import 'models.dart';

class AppStrings {
  static const appTitle = 'Synora';
  static const loginSubtitle = '登录后即可直接和 Synora 对话，由 AI 帮你整理日程、速记与提醒。';
  static const loginButton = '登录';
  static const emailLabel = '邮箱';
  static const passwordLabel = '密码';
  static const emailRequired = '请输入邮箱。';
  static const passwordRequired = '请输入密码。';
  static const loggingIn = '登录中…';
  static const loginRequired = '请先登录。';
  static const loginExpired = '登录已失效，请重新登录。';

  static const settings = '设置';
  static const userInfo = '用户信息';
  static const mySchedules = '我的日程';
  static const myQuickNotes = '我的速记';
  static const conversationHistory = '对话历史';
  static const newConversation = '新对话';
  static const notificationHistory = '通知历史';
  static const logout = '退出登录';
  static const logoutConfirmTitle = '退出登录';
  static const logoutConfirmMessage = '确定退出当前账号吗？';

  static const composerHint = '直接告诉我你的安排、灵感，或发送截图、聊天记录、邮件内容。';
  static const send = '发送';
  static const attach = '附件';
  static const voice = '语音';
  static const voiceComingSoon = '语音输入即将支持。';
  static const loading = '加载中…';
  static const loadFailed = '加载失败，请稍后重试。';
  static const emptyConversation = '开始一段新对话吧。';
  static const emptyConversationHistory = '还没有历史对话。';
  static const emptySchedules = '还没有日程，先和 Synora 说一句吧。';
  static const emptyQuickNotes = '还没有速记，有想法时随时记下来。';
  static const emptyNotifications = '还没有提醒记录。';
  static const noContent = '暂无内容';

  static const currentInputType = '当前输入类型';
  static const chooseInputType = '选择输入方式';
  static const plainText = '普通文本';
  static const pasteChatRecord = '将本次输入标记为聊天记录';
  static const pasteEmail = '将本次输入标记为邮件内容';
  static const importScreenshot = '导入截图/图片';
  static const importPhoto = '拍照导入';
  static const importChatFile = '导入聊天文件';
  static const importEmailFile = '导入邮件文件';
  static const selectedAttachments = '已选附件';
  static const removeAttachment = '移除附件';
  static const sendEmptyMessage = '请输入内容或先添加附件。';
  static const uploadHint = '如果 OCR 失败，可以改为手动粘贴文本。';

  static const scheduleDraft = '日程草稿';
  static const conflictCheck = '冲突检测';
  static const quickNotePreview = '速记预览';
  static const resultCard = '处理结果';
  static const titleField = '标题';
  static const timeField = '时间';
  static const locationField = '地点';
  static const detailsField = '详情';
  static const reminderField = '提醒时间';
  static const tagsField = '标签';
  static const evidenceField = '提取依据';
  static const ambiguityField = '歧义提示';
  static const missingFieldsField = '待补充字段';
  static const parseConfidenceField = '解析置信度';
  static const conflictItemsField = '冲突项';
  static const suggestionsField = '建议时段';
  static const riskLevelField = '风险等级';
  static const confirmSave = '确认保存';
  static const confirmAction = '确认';
  static const submitMissingFields = '提交补充信息';
  static const cancelPendingAction = '取消本次操作';
  static const saveSuccess = '保存成功。';

  static const scheduleListTitle = '我的日程';
  static const quickNoteListTitle = '我的速记';
  static const delete = '删除';
  static const deleteScheduleTitle = '删除日程';
  static const deleteQuickNoteTitle = '删除速记';
  static const deleteScheduleMessage = '删除后将同步移除相关提醒记录，确定继续吗？';
  static const deleteQuickNoteMessage = '删除后无法恢复，确定继续吗？';
  static const cancel = '取消';
  static const deleteDone = '已删除。';

  static const scheduleCreatedSummary = '日程已创建并安排提醒。';
  static const quickNoteSavedSummary = '速记已保存。';

  static String sourceLabel(InputSourceType sourceType) {
    switch (sourceType) {
      case InputSourceType.text:
        return plainText;
      case InputSourceType.screenshot:
        return '截图';
      case InputSourceType.photo:
        return '拍照';
      case InputSourceType.chatRecord:
        return '聊天记录';
      case InputSourceType.email:
        return '邮件内容';
    }
  }

  static String sourceFieldLabel(InputSourceType sourceType) {
    switch (sourceType) {
      case InputSourceType.text:
        return '输入内容';
      case InputSourceType.screenshot:
      case InputSourceType.photo:
        return '补充说明（可选）';
      case InputSourceType.chatRecord:
        return '粘贴聊天记录（可选）';
      case InputSourceType.email:
        return '粘贴邮件正文（可选）';
    }
  }

  static String sourceHint(InputSourceType sourceType) {
    switch (sourceType) {
      case InputSourceType.text:
        return '例如：明天下午三点在学院会议室开教学例会';
      case InputSourceType.screenshot:
        return '可以补充截图来源或备注。';
      case InputSourceType.photo:
        return '可以补充照片背景或上下文。';
      case InputSourceType.chatRecord:
        return '支持直接粘贴聊天内容，也支持导入截图、TXT、JSON、PDF。';
      case InputSourceType.email:
        return '支持直接粘贴邮件正文，也支持导入 EML、截图、PDF。';
    }
  }

  static String missingFieldLabel(String field) {
    switch (field) {
      case 'title':
        return titleField;
      case 'scheduled_at':
        return timeField;
      case 'location':
        return locationField;
      case 'details':
        return detailsField;
      default:
        return field;
    }
  }

  static String ambiguityLabel(String flag) {
    switch (flag) {
      case 'time_ambiguous':
        return '时间可能存在歧义';
      case 'year_inferred':
        return '年份为系统推断';
      case 'location_ambiguous':
        return '地点可能存在歧义';
      default:
        return flag;
    }
  }

  static String riskLevelLabel(String riskLevel) {
    switch (riskLevel) {
      case 'high':
        return '高';
      case 'medium':
        return '中';
      default:
        return '低';
    }
  }

  static String notificationStatus(String status) {
    switch (status) {
      case 'delivered':
        return '已送达';
      case 'failed':
        return '发送失败';
      case 'queued':
        return '排队中';
      case 'sent':
        return '已发送';
      case 'retrying':
        return '重试中';
      default:
        return '处理中';
    }
  }

  static String channelLabel(String channel) {
    switch (channel) {
      case 'email':
        return '邮件';
      case 'wecom_robot':
        return '企业微信群机器人';
      default:
        return channel;
    }
  }

  static String? notificationFailureReason(String channel, String? errorMessage) {
    if (errorMessage == null || errorMessage.trim().isEmpty) {
      return null;
    }
    final message = errorMessage.trim();
    final lower = message.toLowerCase();

    if (channel == 'wecom_robot') {
      if (message.contains('未配置企业微信群机器人')) {
        return '未配置企业微信群机器人，请检查服务端配置。';
      }
      if (message.contains('超时') || lower.contains('timed out')) {
        return '企业微信推送超时，请稍后重试。';
      }
      if (message.contains('网络') || lower.contains('connection') || lower.contains('network')) {
        return '企业微信网络请求失败，请检查服务端网络。';
      }
      if (message.contains('http')) {
        return '企业微信服务暂时不可用，请稍后重试。';
      }
      if (message.contains('无法解析') || lower.contains('json')) {
        return '企业微信返回了异常响应，请稍后重试。';
      }
      final codeMatch = RegExp(r'(\d{4,6})').firstMatch(message);
      if (message.contains('错误码') || message.contains('errcode')) {
        final code = codeMatch?.group(1);
        if (code != null) {
          return '企业微信机器人拒绝了本次消息（错误码 $code）。';
        }
        return '企业微信机器人拒绝了本次消息。';
      }
      return '企业微信推送失败，请稍后重试。';
    }

    if (channel == 'email') {
      if (lower.contains('timed out')) {
        return '邮件发送超时，请稍后重试。';
      }
      if (lower.contains('authentication')) {
        return '邮件服务认证失败，请检查 SMTP 配置。';
      }
      if (lower.contains('connection') || lower.contains('refused')) {
        return '邮件服务连接失败，请检查 SMTP 配置。';
      }
      return '邮件发送失败，请稍后重试。';
    }

    return '发送失败，请稍后重试。';
  }

  static String chatActionSummary(String resultKind) {
    switch (resultKind) {
      case 'schedule_saved':
        return scheduleCreatedSummary;
      case 'quick_note_saved':
        return quickNoteSavedSummary;
      case 'action_cancelled':
        return '已取消本次操作。';
      default:
        return saveSuccess;
    }
  }
}
