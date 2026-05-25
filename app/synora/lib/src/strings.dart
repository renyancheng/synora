class AppStrings {
  static const appTitle = 'Synora';
  static const loginSubtitle = '登录后，直接把事情交给 Synora。';
  static const loginButton = '登录';
  static const emailLabel = '邮箱';
  static const passwordLabel = '密码';
  static const emailRequired = '请输入邮箱';
  static const passwordRequired = '请输入密码';
  static const loggingIn = '登录中...';
  static const loginRequired = '请先登录';
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
  static const logoutConfirmMessage = '确定退出当前账户吗？';

  static const composerHint = '把事情说明白';
  static const attach = '添加附件';
  static const voice = '语音';
  static const send = '发送';
  static const sending = '发送中';
  static const voiceComingSoon = '语音即将支持。';
  static const loading = '处理中...';
  static const loadFailed = '加载失败，请稍后重试。';
  static const emptyConversation = '开始一段新对话吧。';
  static const emptyConversationHistory = '还没有历史对话。';
  static const emptySchedules = '还没有日程，先和 Synora 说一句吧。';
  static const emptyQuickNotes = '还没有速记，有想法时随时记下来。';
  static const emptyNotifications = '还没有提醒记录。';
  static const noContent = '暂无内容';
  static const sendEmptyMessage = '请输入内容或先添加附件。';

  static const attachmentGallery = '相册';
  static const attachmentCamera = '相机';
  static const attachmentFile = '文件';
  static const selectedAttachments = '已选附件';
  static const removeAttachment = '移除附件';
  static const unsupportedMailFile = '可直接粘贴邮件正文，不支持导入邮件文件。';

  static const scheduleTool = '日程';
  static const scheduleToolDescription = '提取时间地点，生成可提醒的安排';
  static const quickNoteTool = '速记';
  static const quickNoteToolDescription = '整理想法与待办，留待稍后回看';
  static const toolAuto = '自动判断';
  static const selectedToolPrefix = '本次工具';

  static const scheduleDraft = '日程草稿';
  static const conflictCheck = '冲突检查';
  static const quickNotePreview = '速记预览';
  static const resultCard = '处理结果';
  static const titleField = '标题';
  static const startField = '开始时间';
  static const endField = '结束时间';
  static const locationField = '地点';
  static const detailsField = '详情';
  static const tagsField = '标签';
  static const evidenceField = '提取依据';
  static const ambiguityField = '歧义提示';
  static const missingFieldsField = '待补充字段';
  static const parseConfidenceField = '解析置信度';
  static const conflictItemsField = '冲突项';
  static const suggestionsField = '建议时段';
  static const riskLevelField = '风险等级';
  static const recurrenceField = '重复规则';
  static const allDayField = '全天';
  static const timeZoneField = '时区';
  static const reminderField = '提醒策略';
  static const lifecycleField = '当前状态';
  static const confirmSave = '确认保存';
  static const confirmAction = '知道了';
  static const submitMissingFields = '提交补充信息';
  static const cancelPendingAction = '取消本次操作';
  static const saveSuccess = '保存成功。';

  static const scheduleListTitle = '我的日程';
  static const quickNoteListTitle = '我的速记';
  static const delete = '删除';
  static const deleteScheduleTitle = '删除日程';
  static const deleteQuickNoteTitle = '删除速记';
  static const deleteScheduleMessage = '删除后会同步移除相关提醒记录，确定继续吗？';
  static const deleteQuickNoteMessage = '删除后无法恢复，确定继续吗？';
  static const cancel = '取消';
  static const deleteDone = '已删除。';

  static const scheduleCreatedSummary = '日程已创建并安排提醒。';
  static const quickNoteSavedSummary = '速记已保存。';

  static const createTimeField = '创建时间';
  static const deliveryTimeField = '送达时间';
  static const failureReasonField = '失败原因';
  static const retryCountField = '重试次数';
  static const generatingInterrupted = '生成中断';

  static String toolLabel(String? tool) {
    switch (tool) {
      case 'schedule':
        return scheduleTool;
      case 'quick_note':
        return quickNoteTool;
      default:
        return toolAuto;
    }
  }

  static String lifecycleLabel(String lifecycle) {
    switch (lifecycle) {
      case 'needs_input':
        return '待补充';
      case 'approval_pending':
        return '待确认';
      case 'conflict_review':
        return '待检查';
      case 'completed':
        return '已完成';
      case 'cancelled':
        return '已取消';
      case 'expired':
        return '已过期';
      case 'superseded':
        return '已替换';
      default:
        return '处理中';
    }
  }

  static String missingFieldLabel(String field) {
    switch (field) {
      case 'title':
        return titleField;
      case 'start_at':
        return startField;
      case 'end_at':
        return endField;
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
        return '年份由系统推断';
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
      if (message.contains('无法解析') || lower.contains('json')) {
        return '企业微信返回了异常响应，请稍后重试。';
      }
      final match = RegExp(r'(\d{4,6})').firstMatch(message);
      if (message.contains('错误码') || message.contains('errcode')) {
        final code = match?.group(1);
        return code == null ? '企业微信机器人拒绝了本次消息。' : '企业微信机器人拒绝了本次消息（错误码 $code）。';
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

  static String chatFailureReason(String? code, String? message) {
    switch (code) {
      case 'llm_not_configured':
        return '智能服务尚未配置，请检查服务端环境变量后重启容器。';
      case 'llm_auth_failed':
        return '智能服务鉴权失败，请检查模型密钥是否有效。';
      case 'llm_rate_limited':
        return '当前请求较多，稍后再试。';
      case 'llm_timeout':
      case 'llm_network_error':
      case 'llm_stream_failed':
        return '本轮回复生成失败，请检查网络后重试。';
      case 'llm_invalid_response':
        return '智能服务返回异常，本轮未完成。';
      case 'conversation_stream_error':
        return '本轮消息流已中断，请重试。';
      default:
        final trimmed = message?.trim() ?? '';
        return trimmed.isEmpty ? '这次处理没有完成，请稍后再试。' : trimmed;
    }
  }
}
