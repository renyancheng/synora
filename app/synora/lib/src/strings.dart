import 'models.dart';

class AppStrings {
  static const appTitle = 'Synora 生活备忘助手';
  static const loginSubtitle = '登录后即可管理日程、速记与提醒历史';
  static const loginButton = '登录';
  static const homeGreeting = '今天也一起把重要的事安排妥当';
  static const newSchedule = '新增日程';
  static const quickNote = '记录速记';
  static const notifications = '通知历史';
  static const refresh = '刷新';
  static const logout = '退出登录';
  static const emptySchedules = '还没有日程，先新建一条吧。';
  static const emptyQuickNotes = '还没有速记，想到什么都可以先记下来。';
  static const emptyNotifications = '还没有提醒记录，创建日程后会显示在这里。';
  static const uploadHint = '如 OCR 失败，可改为手动粘贴文本。';
  static const attachmentUploaded = '附件已加入待上传列表';
  static const loginRequired = '请先登录。';

  static String sourceLabel(InputSourceType sourceType) {
    switch (sourceType) {
      case InputSourceType.text:
        return '纯文本';
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
        return '输入日程内容';
      case InputSourceType.screenshot:
        return '补充说明（可选）';
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
        return '可以补充截图来源或备注';
      case InputSourceType.photo:
        return '可以补充照片说明或上下文';
      case InputSourceType.chatRecord:
        return '支持直接粘贴聊天内容，也可以导入截图/TXT/JSON/PDF';
      case InputSourceType.email:
        return '支持直接粘贴邮件正文，也可以导入 EML/截图/PDF';
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
      if (message.contains('请求超时') || lower.contains('timed out')) {
        return '企业微信推送超时，请稍后重试。';
      }
      if (message.contains('网络请求失败') || lower.contains('connection') || lower.contains('network')) {
        return '企业微信网络请求失败，请检查服务网络。';
      }
      if (message.contains('HTTP 错误')) {
        return '企业微信服务暂时不可用，请稍后重试。';
      }
      if (message.contains('无法解析的响应')) {
        return '企业微信返回了异常响应，请稍后重试。';
      }
      final codeMatch = RegExp(r'错误码\s*(\d+)').firstMatch(message);
      if (message.contains('返回错误码')) {
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
}
