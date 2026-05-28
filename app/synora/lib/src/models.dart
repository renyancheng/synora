import 'dart:typed_data';

enum ConversationTool { schedule, quickNote }

enum VoiceInputState {
  idle,
  awaitingDownloadConfirmation,
  downloading,
  initializing,
  listening,
  processing,
  failed,
}

extension ConversationToolX on ConversationTool {
  String get apiValue {
    switch (this) {
      case ConversationTool.schedule:
        return 'schedule';
      case ConversationTool.quickNote:
        return 'quick_note';
    }
  }

  static ConversationTool? fromApiValue(String? value) {
    switch (value) {
      case 'schedule':
        return ConversationTool.schedule;
      case 'quick_note':
        return ConversationTool.quickNote;
      default:
        return null;
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
  });

  final int attachmentId;
  final String fileName;
  final String contentType;
  final int sizeBytes;

  factory UploadedAttachment.fromJson(Map<String, dynamic> json) {
    return UploadedAttachment(
      attachmentId: json['attachment_id'] as int,
      fileName: json['file_name'] as String,
      contentType: json['content_type'] as String,
      sizeBytes: json['size_bytes'] as int,
    );
  }
}

class AttachmentRef {
  AttachmentRef({
    required this.attachmentId,
    required this.fileName,
    required this.contentType,
    this.sizeBytes,
  });

  final int attachmentId;
  final String fileName;
  final String contentType;
  final int? sizeBytes;

  factory AttachmentRef.fromJson(Map<String, dynamic> json) {
    return AttachmentRef(
      attachmentId: json['attachment_id'] as int,
      fileName: json['file_name'] as String? ?? '',
      contentType: json['content_type'] as String? ?? '',
      sizeBytes: json['size_bytes'] as int?,
    );
  }

  Map<String, dynamic> toJson() {
    return {
      'attachment_id': attachmentId,
      'file_name': fileName,
      'content_type': contentType,
      'size_bytes': sizeBytes,
    };
  }
}

class ComposerAttachment {
  ComposerAttachment.local(this.local)
      : remote = null,
        isLocal = true;

  ComposerAttachment.remote(this.remote)
      : local = null,
        isLocal = false;

  final LocalAttachmentData? local;
  final AttachmentRef? remote;
  final bool isLocal;

  String get fileName => local?.fileName ?? remote?.fileName ?? '';
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

class CurrentSessionInfo {
  CurrentSessionInfo({
    required this.expiresAt,
    required this.user,
  });

  final DateTime expiresAt;
  final UserProfile user;

  factory CurrentSessionInfo.fromJson(Map<String, dynamic> json) {
    return CurrentSessionInfo(
      expiresAt: DateTime.parse(json['expires_at'] as String),
      user: UserProfile.fromJson(json['user'] as Map<String, dynamic>),
    );
  }
}

class UserPreferences {
  UserPreferences({required this.wecomRobotWebhook});

  final String? wecomRobotWebhook;

  factory UserPreferences.fromJson(Map<String, dynamic> json) {
    return UserPreferences(
      wecomRobotWebhook: (json['wecom_robot_webhook'] as String?)?.trim().isEmpty == true
          ? null
          : json['wecom_robot_webhook'] as String?,
    );
  }

  Map<String, dynamic> toJson() => <String, dynamic>{
        'wecom_robot_webhook': wecomRobotWebhook,
      };
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

class EventDateTimeValue {
  EventDateTimeValue({
    required this.dateTime,
    required this.timeZone,
  });

  final DateTime dateTime;
  final String timeZone;

  factory EventDateTimeValue.fromJson(Map<String, dynamic> json) {
    return EventDateTimeValue(
      dateTime: DateTime.parse(json['dateTime'] as String),
      timeZone: json['timeZone'] as String,
    );
  }

  Map<String, dynamic> toJson() {
    return {
      'dateTime': dateTime.toIso8601String(),
      'timeZone': timeZone,
    };
  }

  EventDateTimeValue copyWith({
    DateTime? dateTime,
    String? timeZone,
  }) {
    return EventDateTimeValue(
      dateTime: dateTime ?? this.dateTime,
      timeZone: timeZone ?? this.timeZone,
    );
  }
}

class ScheduleDraft {
  ScheduleDraft({
    required this.title,
    required this.details,
    required this.sourceText,
    required this.isAllDay,
    required this.start,
    required this.end,
    required this.recurrence,
    required this.reminderPreset,
    required this.sourceAttachmentIds,
    required this.parseConfidence,
    required this.evidenceDigest,
    this.location,
  });

  final String title;
  final String? location;
  final String details;
  final String sourceText;
  final bool isAllDay;
  final EventDateTimeValue start;
  final EventDateTimeValue end;
  final List<String> recurrence;
  final String reminderPreset;
  final List<int> sourceAttachmentIds;
  final double parseConfidence;
  final List<String> evidenceDigest;

  factory ScheduleDraft.fromJson(Map<String, dynamic> json) {
    return ScheduleDraft(
      title: json['title'] as String? ?? '',
      location: json['location'] as String?,
      details: json['details'] as String? ?? '',
      sourceText: json['source_text'] as String? ?? '',
      isAllDay: json['isAllDay'] as bool? ?? false,
      start: EventDateTimeValue.fromJson(json['start'] as Map<String, dynamic>),
      end: EventDateTimeValue.fromJson(json['end'] as Map<String, dynamic>),
      recurrence: (json['recurrence'] as List<dynamic>? ?? <dynamic>[]).cast<String>(),
      reminderPreset: json['reminder_preset'] as String? ?? 'previous_day_1700',
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
      'isAllDay': isAllDay,
      'start': start.toJson(),
      'end': end.toJson(),
      'recurrence': recurrence,
      'reminder_preset': reminderPreset,
      'source_attachment_ids': sourceAttachmentIds,
      'parse_confidence': parseConfidence,
      'evidence_digest': evidenceDigest,
    };
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
    required this.start,
    required this.end,
    this.location,
  });

  final int scheduleId;
  final String title;
  final EventDateTimeValue start;
  final EventDateTimeValue end;
  final String? location;

  factory ConflictItem.fromJson(Map<String, dynamic> json) {
    return ConflictItem(
      scheduleId: json['schedule_id'] as int,
      title: json['title'] as String,
      start: EventDateTimeValue.fromJson(json['start'] as Map<String, dynamic>),
      end: EventDateTimeValue.fromJson(json['end'] as Map<String, dynamic>),
      location: json['location'] as String?,
    );
  }
}

class ConflictSuggestion {
  ConflictSuggestion({
    required this.label,
    required this.start,
    required this.end,
  });

  final String label;
  final EventDateTimeValue start;
  final EventDateTimeValue end;

  factory ConflictSuggestion.fromJson(Map<String, dynamic> json) {
    return ConflictSuggestion(
      label: json['label'] as String,
      start: EventDateTimeValue.fromJson(json['start'] as Map<String, dynamic>),
      end: EventDateTimeValue.fromJson(json['end'] as Map<String, dynamic>),
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

class ScheduleEditPreviewResult {
  ScheduleEditPreviewResult({
    required this.scheduleId,
    required this.draft,
    required this.conflictItems,
    required this.suggestions,
    required this.riskLevel,
    required this.approval,
  });

  final int scheduleId;
  final ScheduleDraft draft;
  final List<ConflictItem> conflictItems;
  final List<ConflictSuggestion> suggestions;
  final String riskLevel;
  final ApprovalInfo approval;

  factory ScheduleEditPreviewResult.fromJson(Map<String, dynamic> json) {
    return ScheduleEditPreviewResult(
      scheduleId: json['schedule_id'] as int,
      draft: ScheduleDraft.fromJson(json['draft'] as Map<String, dynamic>),
      conflictItems: (json['conflict_items'] as List<dynamic>? ?? <dynamic>[])
          .map((item) => ConflictItem.fromJson(item as Map<String, dynamic>))
          .toList(),
      suggestions: (json['suggestions'] as List<dynamic>? ?? <dynamic>[])
          .map((item) => ConflictSuggestion.fromJson(item as Map<String, dynamic>))
          .toList(),
      riskLevel: json['risk_level'] as String? ?? 'low',
      approval: ApprovalInfo.fromJson(json['approval'] as Map<String, dynamic>),
    );
  }
}

class ScheduleEditConfirmResult {
  ScheduleEditConfirmResult({
    required this.schedule,
    required this.reminderJobs,
  });

  final ScheduleItem schedule;
  final List<ReminderJobInfo> reminderJobs;

  factory ScheduleEditConfirmResult.fromJson(Map<String, dynamic> json) {
    return ScheduleEditConfirmResult(
      schedule: ScheduleItem.fromJson(json['schedule'] as Map<String, dynamic>),
      reminderJobs: (json['reminder_jobs'] as List<dynamic>? ?? <dynamic>[])
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
    required this.sourceText,
    required this.isAllDay,
    required this.start,
    required this.end,
    required this.recurrence,
    required this.reminderPreset,
    required this.sourceAttachmentIds,
    required this.reminderOffsetsMinutes,
    required this.status,
    required this.createdAt,
    required this.parseConfidence,
    this.location,
  });

  final int id;
  final String title;
  final String? location;
  final String details;
  final String sourceText;
  final bool isAllDay;
  final EventDateTimeValue start;
  final EventDateTimeValue end;
  final List<String> recurrence;
  final String reminderPreset;
  final List<int> sourceAttachmentIds;
  final List<int> reminderOffsetsMinutes;
  final String status;
  final DateTime createdAt;
  final double parseConfidence;

  factory ScheduleItem.fromJson(Map<String, dynamic> json) {
    return ScheduleItem(
      id: json['id'] as int,
      title: json['title'] as String,
      location: json['location'] as String?,
      details: json['details'] as String,
      sourceText: json['source_text'] as String? ?? '',
      isAllDay: json['isAllDay'] as bool? ?? false,
      start: EventDateTimeValue.fromJson(json['start'] as Map<String, dynamic>),
      end: EventDateTimeValue.fromJson(json['end'] as Map<String, dynamic>),
      recurrence: (json['recurrence'] as List<dynamic>? ?? <dynamic>[]).cast<String>(),
      reminderPreset: json['reminder_preset'] as String? ?? 'previous_day_1700',
      sourceAttachmentIds: (json['source_attachment_ids'] as List<dynamic>? ?? <dynamic>[]).cast<int>(),
      reminderOffsetsMinutes: (json['reminder_offsets_minutes'] as List<dynamic>? ?? <dynamic>[]).cast<int>(),
      status: json['status'] as String,
      createdAt: DateTime.parse(json['created_at'] as String),
      parseConfidence: (json['parse_confidence'] as num? ?? 0).toDouble(),
    );
  }

  ScheduleItem copyWith({
    int? id,
    String? title,
    String? location,
    String? details,
    String? sourceText,
    bool? isAllDay,
    EventDateTimeValue? start,
    EventDateTimeValue? end,
    List<String>? recurrence,
    String? reminderPreset,
    List<int>? sourceAttachmentIds,
    List<int>? reminderOffsetsMinutes,
    String? status,
    DateTime? createdAt,
    double? parseConfidence,
  }) {
    return ScheduleItem(
      id: id ?? this.id,
      title: title ?? this.title,
      location: location ?? this.location,
      details: details ?? this.details,
      sourceText: sourceText ?? this.sourceText,
      isAllDay: isAllDay ?? this.isAllDay,
      start: start ?? this.start,
      end: end ?? this.end,
      recurrence: recurrence ?? this.recurrence,
      reminderPreset: reminderPreset ?? this.reminderPreset,
      sourceAttachmentIds: sourceAttachmentIds ?? this.sourceAttachmentIds,
      reminderOffsetsMinutes: reminderOffsetsMinutes ?? this.reminderOffsetsMinutes,
      status: status ?? this.status,
      createdAt: createdAt ?? this.createdAt,
      parseConfidence: parseConfidence ?? this.parseConfidence,
    );
  }
}

class QuickNoteDraftPreview {
  QuickNoteDraftPreview({
    required this.normalizedContent,
    required this.previewTags,
    required this.attachmentIds,
    required this.evidenceDigest,
    required this.approval,
  });

  final String normalizedContent;
  final List<String> previewTags;
  final List<int> attachmentIds;
  final List<String> evidenceDigest;
  final ApprovalInfo approval;

  factory QuickNoteDraftPreview.fromJson(Map<String, dynamic> json) {
    return QuickNoteDraftPreview(
      normalizedContent: json['normalized_content'] as String,
      previewTags: (json['preview_tags'] as List<dynamic>).cast<String>(),
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
    required this.sourceAttachmentIds,
  });

  final int id;
  final String content;
  final List<String> tags;
  final DateTime createdAt;
  final List<int> sourceAttachmentIds;

  factory QuickNoteItem.fromJson(Map<String, dynamic> json) {
    return QuickNoteItem(
      id: json['id'] as int,
      content: json['content'] as String,
      tags: (json['tags'] as List<dynamic>).cast<String>(),
      createdAt: DateTime.parse(json['created_at'] as String),
      sourceAttachmentIds: (json['source_attachment_ids'] as List<dynamic>? ?? <dynamic>[]).cast<int>(),
    );
  }
}

class QuickNoteTagItem {
  QuickNoteTagItem({
    required this.tag,
    required this.count,
  });

  final String tag;
  final int count;

  factory QuickNoteTagItem.fromJson(Map<String, dynamic> json) {
    return QuickNoteTagItem(
      tag: json['tag'] as String,
      count: json['count'] as int,
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

class MemoryItem {
  MemoryItem({
    required this.id,
    required this.memoryType,
    required this.title,
    required this.content,
    required this.sourceKind,
    required this.isActive,
    required this.updatedAt,
    this.sourceRefId,
  });

  final int id;
  final String memoryType;
  final String title;
  final String content;
  final String sourceKind;
  final String? sourceRefId;
  final bool isActive;
  final DateTime updatedAt;

  factory MemoryItem.fromJson(Map<String, dynamic> json) {
    return MemoryItem(
      id: json['id'] as int,
      memoryType: json['memory_type'] as String,
      title: json['title'] as String,
      content: json['content'] as String,
      sourceKind: json['source_kind'] as String,
      sourceRefId: json['source_ref_id'] as String?,
      isActive: json['is_active'] as bool? ?? true,
      updatedAt: DateTime.parse(json['updated_at'] as String),
    );
  }
}

class MemoryListResult {
  MemoryListResult({
    required this.summary,
    required this.items,
  });

  final String summary;
  final List<MemoryItem> items;

  factory MemoryListResult.fromJson(Map<String, dynamic> json) {
    return MemoryListResult(
      summary: json['summary'] as String? ?? '',
      items: (json['items'] as List<dynamic>? ?? <dynamic>[])
          .map((item) => MemoryItem.fromJson(item as Map<String, dynamic>))
          .toList(),
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

  ConversationThreadItem copyWith({
    int? id,
    String? title,
    DateTime? createdAt,
    DateTime? updatedAt,
    DateTime? lastMessageAt,
  }) {
    return ConversationThreadItem(
      id: id ?? this.id,
      title: title ?? this.title,
      createdAt: createdAt ?? this.createdAt,
      updatedAt: updatedAt ?? this.updatedAt,
      lastMessageAt: lastMessageAt ?? this.lastMessageAt,
    );
  }
}

class ConversationMessageItem {
  ConversationMessageItem({
    required this.id,
    required this.role,
    required this.messageType,
    required this.status,
    required this.structuredPayload,
    required this.createdAt,
    this.textContent,
    this.actionGroupId,
    this.revision = 1,
    this.localAttachments = const <LocalAttachmentData>[],
  });

  final int id;
  final String role;
  final String messageType;
  final String status;
  final String? textContent;
  final Map<String, dynamic> structuredPayload;
  final String? actionGroupId;
  final int revision;
  final DateTime createdAt;
  final List<LocalAttachmentData> localAttachments;

  bool get isUser => role == 'user';
  bool get isAssistant => role == 'assistant';

  ConversationTool? get selectedTool => ConversationToolX.fromApiValue(structuredPayload['selected_tool'] as String?);

  List<AttachmentRef> get attachmentRefs {
    final raw = structuredPayload['attachment_refs'] as List<dynamic>? ?? <dynamic>[];
    return raw
        .whereType<Map<String, dynamic>>()
        .map(AttachmentRef.fromJson)
        .toList();
  }

  factory ConversationMessageItem.fromJson(Map<String, dynamic> json) {
    return ConversationMessageItem(
      id: json['id'] as int,
      role: json['role'] as String,
      messageType: json['message_type'] as String,
      status: json['status'] as String? ?? 'completed',
      textContent: json['text_content'] as String?,
      structuredPayload: (json['structured_payload'] as Map<String, dynamic>? ?? <String, dynamic>{}),
      actionGroupId: json['action_group_id'] as String?,
      revision: json['revision'] as int? ?? 1,
      createdAt: DateTime.parse(json['created_at'] as String),
    );
  }

  factory ConversationMessageItem.local({
    required int id,
    required String role,
    required String messageType,
    required String status,
    String? textContent,
    Map<String, dynamic> structuredPayload = const <String, dynamic>{},
    List<LocalAttachmentData> localAttachments = const <LocalAttachmentData>[],
  }) {
    return ConversationMessageItem(
      id: id,
      role: role,
      messageType: messageType,
      status: status,
      textContent: textContent,
      structuredPayload: structuredPayload,
      createdAt: DateTime.now(),
      localAttachments: localAttachments,
    );
  }

  ConversationMessageItem copyWith({
    int? id,
    String? role,
    String? messageType,
    String? status,
    String? textContent,
    Map<String, dynamic>? structuredPayload,
    String? actionGroupId,
    int? revision,
    DateTime? createdAt,
    List<LocalAttachmentData>? localAttachments,
  }) {
    return ConversationMessageItem(
      id: id ?? this.id,
      role: role ?? this.role,
      messageType: messageType ?? this.messageType,
      status: status ?? this.status,
      textContent: textContent ?? this.textContent,
      structuredPayload: structuredPayload ?? this.structuredPayload,
      actionGroupId: actionGroupId ?? this.actionGroupId,
      revision: revision ?? this.revision,
      createdAt: createdAt ?? this.createdAt,
      localAttachments: localAttachments ?? this.localAttachments,
    );
  }
}

class ConversationSendAcceptedResult {
  ConversationSendAcceptedResult({
    required this.conversation,
    required this.userMessage,
    required this.assistantMessageId,
    required this.streamId,
  });

  final ConversationThreadItem conversation;
  final ConversationMessageItem userMessage;
  final int assistantMessageId;
  final String streamId;

  factory ConversationSendAcceptedResult.fromJson(Map<String, dynamic> json) {
    return ConversationSendAcceptedResult(
      conversation: ConversationThreadItem.fromJson(json['conversation'] as Map<String, dynamic>),
      userMessage: ConversationMessageItem.fromJson(json['user_message'] as Map<String, dynamic>),
      assistantMessageId: json['assistant_message_id'] as int,
      streamId: json['stream_id'] as String,
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

class ConversationRewindResult {
  ConversationRewindResult({
    required this.conversation,
    required this.restoredMessage,
  });

  final ConversationThreadItem conversation;
  final ConversationMessageItem restoredMessage;

  factory ConversationRewindResult.fromJson(Map<String, dynamic> json) {
    return ConversationRewindResult(
      conversation: ConversationThreadItem.fromJson(json['conversation'] as Map<String, dynamic>),
      restoredMessage: ConversationMessageItem.fromJson(json['restored_message'] as Map<String, dynamic>),
    );
  }
}

class ConversationStreamEvent {
  ConversationStreamEvent({
    required this.event,
    required this.data,
  });

  final String event;
  final Map<String, dynamic> data;
}
