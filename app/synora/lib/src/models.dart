import 'dart:typed_data';

enum InputSourceType { text, screenshot, photo, chatRecord, email }

extension InputSourceTypeX on InputSourceType {
  String get apiValue {
    switch (this) {
      case InputSourceType.text:
        return 'text';
      case InputSourceType.screenshot:
        return 'screenshot';
      case InputSourceType.photo:
        return 'photo';
      case InputSourceType.chatRecord:
        return 'chat_record';
      case InputSourceType.email:
        return 'email';
    }
  }

  static InputSourceType fromApiValue(String value) {
    switch (value) {
      case 'screenshot':
        return InputSourceType.screenshot;
      case 'photo':
        return InputSourceType.photo;
      case 'chat_record':
        return InputSourceType.chatRecord;
      case 'email':
        return InputSourceType.email;
      default:
        return InputSourceType.text;
    }
  }
}

class LocalAttachmentData {
  LocalAttachmentData({
    required this.fileName,
    required this.bytes,
  });

  final String fileName;
  final Uint8List bytes;
}

class UploadedAttachment {
  UploadedAttachment({
    required this.attachmentId,
    required this.fileName,
    required this.contentType,
    required this.sizeBytes,
    required this.sourceType,
  });

  final int attachmentId;
  final String fileName;
  final String contentType;
  final int sizeBytes;
  final InputSourceType sourceType;

  factory UploadedAttachment.fromJson(Map<String, dynamic> json) {
    return UploadedAttachment(
      attachmentId: json['attachment_id'] as int,
      fileName: json['file_name'] as String,
      contentType: json['content_type'] as String,
      sizeBytes: json['size_bytes'] as int,
      sourceType: InputSourceTypeX.fromApiValue(json['source_type'] as String),
    );
  }
}

class UserProfile {
  UserProfile({
    required this.id,
    required this.email,
    required this.displayName,
  });

  final int id;
  final String email;
  final String displayName;

  factory UserProfile.fromJson(Map<String, dynamic> json) {
    return UserProfile(
      id: json['id'] as int,
      email: json['email'] as String,
      displayName: json['display_name'] as String,
    );
  }
}

class SessionInfo {
  SessionInfo({
    required this.accessToken,
    required this.expiresAt,
    required this.user,
  });

  final String accessToken;
  final DateTime expiresAt;
  final UserProfile user;

  factory SessionInfo.fromJson(Map<String, dynamic> json) {
    return SessionInfo(
      accessToken: json['access_token'] as String,
      expiresAt: DateTime.parse(json['expires_at'] as String),
      user: UserProfile.fromJson(json['user'] as Map<String, dynamic>),
    );
  }
}

class ApprovalInfo {
  ApprovalInfo({
    required this.approvalToken,
    required this.action,
    required this.expiresAt,
    required this.draftHash,
  });

  final String approvalToken;
  final String action;
  final DateTime expiresAt;
  final String draftHash;

  factory ApprovalInfo.fromJson(Map<String, dynamic> json) {
    return ApprovalInfo(
      approvalToken: json['approval_token'] as String,
      action: json['action'] as String,
      expiresAt: DateTime.parse(json['expires_at'] as String),
      draftHash: json['draft_hash'] as String,
    );
  }
}

class ScheduleDraft {
  ScheduleDraft({
    required this.title,
    required this.details,
    required this.sourceText,
    required this.durationMinutes,
    required this.sourceType,
    required this.sourceAttachmentIds,
    required this.parseConfidence,
    required this.evidenceDigest,
    this.location,
    this.scheduledAt,
    this.reminderAt,
  });

  final String title;
  final String? location;
  final String details;
  final String sourceText;
  final DateTime? scheduledAt;
  final int durationMinutes;
  final DateTime? reminderAt;
  final InputSourceType sourceType;
  final List<int> sourceAttachmentIds;
  final double parseConfidence;
  final List<String> evidenceDigest;

  factory ScheduleDraft.fromJson(Map<String, dynamic> json) {
    return ScheduleDraft(
      title: json['title'] as String? ?? '',
      location: json['location'] as String?,
      details: json['details'] as String? ?? '',
      sourceText: json['source_text'] as String? ?? '',
      scheduledAt: json['scheduled_at'] == null ? null : DateTime.parse(json['scheduled_at'] as String),
      durationMinutes: json['duration_minutes'] as int? ?? 60,
      reminderAt: json['reminder_at'] == null ? null : DateTime.parse(json['reminder_at'] as String),
      sourceType: InputSourceTypeX.fromApiValue(json['source_type'] as String? ?? 'text'),
      sourceAttachmentIds: (json['source_attachment_ids'] as List<dynamic>? ?? <dynamic>[]).cast<int>(),
      parseConfidence: (json['parse_confidence'] as num? ?? 0).toDouble(),
      evidenceDigest: (json['evidence_digest'] as List<dynamic>? ?? <dynamic>[]).cast<String>(),
    );
  }

  Map<String, dynamic> toJson() {
    return {
      'title': title,
      'location': location,
      'details': details,
      'source_text': sourceText,
      'scheduled_at': scheduledAt?.toUtc().toIso8601String(),
      'duration_minutes': durationMinutes,
      'reminder_at': reminderAt?.toUtc().toIso8601String(),
      'source_type': sourceType.apiValue,
      'source_attachment_ids': sourceAttachmentIds,
      'parse_confidence': parseConfidence,
      'evidence_digest': evidenceDigest,
    };
  }

  ScheduleDraft copyWith({
    String? title,
    String? location,
    String? details,
    String? sourceText,
    DateTime? scheduledAt,
    int? durationMinutes,
    DateTime? reminderAt,
    InputSourceType? sourceType,
    List<int>? sourceAttachmentIds,
    double? parseConfidence,
    List<String>? evidenceDigest,
  }) {
    return ScheduleDraft(
      title: title ?? this.title,
      location: location ?? this.location,
      details: details ?? this.details,
      sourceText: sourceText ?? this.sourceText,
      scheduledAt: scheduledAt ?? this.scheduledAt,
      durationMinutes: durationMinutes ?? this.durationMinutes,
      reminderAt: reminderAt ?? this.reminderAt,
      sourceType: sourceType ?? this.sourceType,
      sourceAttachmentIds: sourceAttachmentIds ?? this.sourceAttachmentIds,
      parseConfidence: parseConfidence ?? this.parseConfidence,
      evidenceDigest: evidenceDigest ?? this.evidenceDigest,
    );
  }
}

class ScheduleDraftResult {
  ScheduleDraftResult({
    required this.draft,
    required this.draftHash,
    required this.missingFields,
    required this.ambiguityFlags,
    required this.evidenceDigest,
    required this.parseConfidence,
  });

  final ScheduleDraft draft;
  final String draftHash;
  final List<String> missingFields;
  final List<String> ambiguityFlags;
  final List<String> evidenceDigest;
  final double parseConfidence;

  factory ScheduleDraftResult.fromJson(Map<String, dynamic> json) {
    return ScheduleDraftResult(
      draft: ScheduleDraft.fromJson(json['draft'] as Map<String, dynamic>),
      draftHash: json['draft_hash'] as String,
      missingFields: (json['missing_fields'] as List<dynamic>).cast<String>(),
      ambiguityFlags: (json['ambiguity_flags'] as List<dynamic>).cast<String>(),
      evidenceDigest: (json['evidence_digest'] as List<dynamic>? ?? <dynamic>[]).cast<String>(),
      parseConfidence: (json['parse_confidence'] as num? ?? 0).toDouble(),
    );
  }
}

class ConflictItem {
  ConflictItem({
    required this.scheduleId,
    required this.title,
    required this.startsAt,
    required this.endsAt,
    this.location,
  });

  final int scheduleId;
  final String title;
  final DateTime startsAt;
  final DateTime endsAt;
  final String? location;

  factory ConflictItem.fromJson(Map<String, dynamic> json) {
    return ConflictItem(
      scheduleId: json['schedule_id'] as int,
      title: json['title'] as String,
      startsAt: DateTime.parse(json['starts_at'] as String),
      endsAt: DateTime.parse(json['ends_at'] as String),
      location: json['location'] as String?,
    );
  }
}

class ConflictSuggestion {
  ConflictSuggestion({
    required this.label,
    required this.candidateStart,
    required this.candidateEnd,
  });

  final String label;
  final DateTime candidateStart;
  final DateTime candidateEnd;

  factory ConflictSuggestion.fromJson(Map<String, dynamic> json) {
    return ConflictSuggestion(
      label: json['label'] as String,
      candidateStart: DateTime.parse(json['candidate_start'] as String),
      candidateEnd: DateTime.parse(json['candidate_end'] as String),
    );
  }
}

class ConflictCheckResult {
  ConflictCheckResult({
    required this.conflictItems,
    required this.suggestions,
    required this.riskLevel,
    required this.approval,
  });

  final List<ConflictItem> conflictItems;
  final List<ConflictSuggestion> suggestions;
  final String riskLevel;
  final ApprovalInfo approval;

  factory ConflictCheckResult.fromJson(Map<String, dynamic> json) {
    return ConflictCheckResult(
      conflictItems: (json['conflict_items'] as List<dynamic>)
          .map((item) => ConflictItem.fromJson(item as Map<String, dynamic>))
          .toList(),
      suggestions: (json['suggestions'] as List<dynamic>)
          .map((item) => ConflictSuggestion.fromJson(item as Map<String, dynamic>))
          .toList(),
      riskLevel: json['risk_level'] as String,
      approval: ApprovalInfo.fromJson(json['approval'] as Map<String, dynamic>),
    );
  }
}

class ReminderJobInfo {
  ReminderJobInfo({
    required this.id,
    required this.channel,
    required this.scheduledFor,
    required this.status,
  });

  final int id;
  final String channel;
  final DateTime scheduledFor;
  final String status;

  factory ReminderJobInfo.fromJson(Map<String, dynamic> json) {
    return ReminderJobInfo(
      id: json['id'] as int,
      channel: json['channel'] as String,
      scheduledFor: DateTime.parse(json['scheduled_for'] as String),
      status: json['status'] as String,
    );
  }
}

class ScheduleConfirmResult {
  ScheduleConfirmResult({
    required this.scheduleId,
    required this.reminderJobs,
  });

  final int scheduleId;
  final List<ReminderJobInfo> reminderJobs;

  factory ScheduleConfirmResult.fromJson(Map<String, dynamic> json) {
    return ScheduleConfirmResult(
      scheduleId: json['schedule_id'] as int,
      reminderJobs: (json['reminder_jobs'] as List<dynamic>)
          .map((item) => ReminderJobInfo.fromJson(item as Map<String, dynamic>))
          .toList(),
    );
  }
}

class ScheduleItem {
  ScheduleItem({
    required this.id,
    required this.title,
    required this.details,
    required this.scheduledAt,
    required this.durationMinutes,
    required this.reminderAt,
    required this.status,
    required this.createdAt,
    required this.sourceType,
    required this.parseConfidence,
    this.location,
  });

  final int id;
  final String title;
  final String? location;
  final String details;
  final DateTime scheduledAt;
  final int durationMinutes;
  final DateTime reminderAt;
  final String status;
  final DateTime createdAt;
  final InputSourceType sourceType;
  final double parseConfidence;

  factory ScheduleItem.fromJson(Map<String, dynamic> json) {
    return ScheduleItem(
      id: json['id'] as int,
      title: json['title'] as String,
      location: json['location'] as String?,
      details: json['details'] as String,
      scheduledAt: DateTime.parse(json['scheduled_at'] as String),
      durationMinutes: json['duration_minutes'] as int,
      reminderAt: DateTime.parse(json['reminder_at'] as String),
      status: json['status'] as String,
      createdAt: DateTime.parse(json['created_at'] as String),
      sourceType: InputSourceTypeX.fromApiValue(json['source_type'] as String? ?? 'text'),
      parseConfidence: (json['parse_confidence'] as num? ?? 0).toDouble(),
    );
  }
}

class QuickNoteDraftPreview {
  QuickNoteDraftPreview({
    required this.normalizedContent,
    required this.previewTags,
    required this.sourceType,
    required this.attachmentIds,
    required this.evidenceDigest,
    required this.approval,
  });

  final String normalizedContent;
  final List<String> previewTags;
  final InputSourceType sourceType;
  final List<int> attachmentIds;
  final List<String> evidenceDigest;
  final ApprovalInfo approval;

  factory QuickNoteDraftPreview.fromJson(Map<String, dynamic> json) {
    return QuickNoteDraftPreview(
      normalizedContent: json['normalized_content'] as String,
      previewTags: (json['preview_tags'] as List<dynamic>).cast<String>(),
      sourceType: InputSourceTypeX.fromApiValue(json['source_type'] as String),
      attachmentIds: (json['attachment_ids'] as List<dynamic>).cast<int>(),
      evidenceDigest: (json['evidence_digest'] as List<dynamic>? ?? <dynamic>[]).cast<String>(),
      approval: ApprovalInfo.fromJson(json['approval'] as Map<String, dynamic>),
    );
  }
}

class QuickNoteSaveResult {
  QuickNoteSaveResult({
    required this.noteId,
    required this.topicTags,
  });

  final int noteId;
  final List<String> topicTags;

  factory QuickNoteSaveResult.fromJson(Map<String, dynamic> json) {
    return QuickNoteSaveResult(
      noteId: json['note_id'] as int,
      topicTags: (json['topic_tags'] as List<dynamic>).cast<String>(),
    );
  }
}

class QuickNoteItem {
  QuickNoteItem({
    required this.id,
    required this.content,
    required this.tags,
    required this.createdAt,
    required this.sourceType,
  });

  final int id;
  final String content;
  final List<String> tags;
  final DateTime createdAt;
  final InputSourceType sourceType;

  factory QuickNoteItem.fromJson(Map<String, dynamic> json) {
    return QuickNoteItem(
      id: json['id'] as int,
      content: json['content'] as String,
      tags: (json['tags'] as List<dynamic>).cast<String>(),
      createdAt: DateTime.parse(json['created_at'] as String),
      sourceType: InputSourceTypeX.fromApiValue(json['source_type'] as String? ?? 'text'),
    );
  }
}

class NotificationItem {
  NotificationItem({
    required this.id,
    required this.channel,
    required this.provider,
    required this.recipient,
    required this.subject,
    required this.status,
    required this.retryCount,
    required this.createdAt,
    this.errorMessage,
    this.deliveredAt,
  });

  final int id;
  final String channel;
  final String provider;
  final String recipient;
  final String subject;
  final String status;
  final int retryCount;
  final String? errorMessage;
  final DateTime createdAt;
  final DateTime? deliveredAt;

  factory NotificationItem.fromJson(Map<String, dynamic> json) {
    return NotificationItem(
      id: json['id'] as int,
      channel: json['channel'] as String,
      provider: json['provider'] as String? ?? '',
      recipient: json['recipient'] as String,
      subject: json['subject'] as String,
      status: json['status'] as String,
      retryCount: json['retry_count'] as int? ?? 0,
      errorMessage: json['error_message'] as String?,
      createdAt: DateTime.parse(json['created_at'] as String),
      deliveredAt: json['delivered_at'] == null ? null : DateTime.parse(json['delivered_at'] as String),
    );
  }
}

class ConversationThreadItem {
  ConversationThreadItem({
    required this.id,
    required this.title,
    required this.createdAt,
    required this.updatedAt,
    required this.lastMessageAt,
  });

  final int id;
  final String title;
  final DateTime createdAt;
  final DateTime updatedAt;
  final DateTime lastMessageAt;

  factory ConversationThreadItem.fromJson(Map<String, dynamic> json) {
    return ConversationThreadItem(
      id: json['id'] as int,
      title: json['title'] as String,
      createdAt: DateTime.parse(json['created_at'] as String),
      updatedAt: DateTime.parse(json['updated_at'] as String),
      lastMessageAt: DateTime.parse(json['last_message_at'] as String),
    );
  }
}

class ConversationMessageItem {
  ConversationMessageItem({
    required this.id,
    required this.role,
    required this.messageType,
    required this.structuredPayload,
    required this.createdAt,
    this.textContent,
  });

  final int id;
  final String role;
  final String messageType;
  final String? textContent;
  final Map<String, dynamic> structuredPayload;
  final DateTime createdAt;

  bool get isUser => role == 'user';
  bool get isAssistant => role == 'assistant';

  factory ConversationMessageItem.fromJson(Map<String, dynamic> json) {
    return ConversationMessageItem(
      id: json['id'] as int,
      role: json['role'] as String,
      messageType: json['message_type'] as String,
      textContent: json['text_content'] as String?,
      structuredPayload: (json['structured_payload'] as Map<String, dynamic>? ?? <String, dynamic>{}),
      createdAt: DateTime.parse(json['created_at'] as String),
    );
  }
}

class ConversationSendMessageResult {
  ConversationSendMessageResult({
    required this.conversation,
    required this.userMessage,
    required this.assistantMessages,
  });

  final ConversationThreadItem conversation;
  final ConversationMessageItem userMessage;
  final List<ConversationMessageItem> assistantMessages;

  factory ConversationSendMessageResult.fromJson(Map<String, dynamic> json) {
    return ConversationSendMessageResult(
      conversation: ConversationThreadItem.fromJson(json['conversation'] as Map<String, dynamic>),
      userMessage: ConversationMessageItem.fromJson(json['user_message'] as Map<String, dynamic>),
      assistantMessages: (json['assistant_messages'] as List<dynamic>)
          .map((item) => ConversationMessageItem.fromJson(item as Map<String, dynamic>))
          .toList(),
    );
  }
}

class ConversationActionResult {
  ConversationActionResult({
    required this.conversation,
    required this.assistantMessages,
  });

  final ConversationThreadItem conversation;
  final List<ConversationMessageItem> assistantMessages;

  factory ConversationActionResult.fromJson(Map<String, dynamic> json) {
    return ConversationActionResult(
      conversation: ConversationThreadItem.fromJson(json['conversation'] as Map<String, dynamic>),
      assistantMessages: (json['assistant_messages'] as List<dynamic>)
          .map((item) => ConversationMessageItem.fromJson(item as Map<String, dynamic>))
          .toList(),
    );
  }
}
