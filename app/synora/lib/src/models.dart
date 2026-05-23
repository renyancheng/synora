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

  factory ScheduleDraft.fromJson(Map<String, dynamic> json) {
    return ScheduleDraft(
      title: json['title'] as String? ?? '',
      location: json['location'] as String?,
      details: json['details'] as String? ?? '',
      sourceText: json['source_text'] as String? ?? '',
      scheduledAt: json['scheduled_at'] == null ? null : DateTime.parse(json['scheduled_at'] as String),
      durationMinutes: json['duration_minutes'] as int? ?? 60,
      reminderAt: json['reminder_at'] == null ? null : DateTime.parse(json['reminder_at'] as String),
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
  }) {
    return ScheduleDraft(
      title: title ?? this.title,
      location: location ?? this.location,
      details: details ?? this.details,
      sourceText: sourceText ?? this.sourceText,
      scheduledAt: scheduledAt ?? this.scheduledAt,
      durationMinutes: durationMinutes ?? this.durationMinutes,
      reminderAt: reminderAt ?? this.reminderAt,
    );
  }
}

class ScheduleDraftResult {
  ScheduleDraftResult({
    required this.draft,
    required this.draftHash,
    required this.missingFields,
    required this.ambiguityFlags,
  });

  final ScheduleDraft draft;
  final String draftHash;
  final List<String> missingFields;
  final List<String> ambiguityFlags;

  factory ScheduleDraftResult.fromJson(Map<String, dynamic> json) {
    return ScheduleDraftResult(
      draft: ScheduleDraft.fromJson(json['draft'] as Map<String, dynamic>),
      draftHash: json['draft_hash'] as String,
      missingFields: (json['missing_fields'] as List<dynamic>).cast<String>(),
      ambiguityFlags: (json['ambiguity_flags'] as List<dynamic>).cast<String>(),
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
    );
  }
}

class QuickNotePreview {
  QuickNotePreview({
    required this.previewTags,
    required this.approval,
  });

  final List<String> previewTags;
  final ApprovalInfo approval;

  factory QuickNotePreview.fromJson(Map<String, dynamic> json) {
    return QuickNotePreview(
      previewTags: (json['preview_tags'] as List<dynamic>).cast<String>(),
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
  });

  final int id;
  final String content;
  final List<String> tags;
  final DateTime createdAt;

  factory QuickNoteItem.fromJson(Map<String, dynamic> json) {
    return QuickNoteItem(
      id: json['id'] as int,
      content: json['content'] as String,
      tags: (json['tags'] as List<dynamic>).cast<String>(),
      createdAt: DateTime.parse(json['created_at'] as String),
    );
  }
}

class NotificationItem {
  NotificationItem({
    required this.id,
    required this.channel,
    required this.recipient,
    required this.subject,
    required this.status,
    required this.createdAt,
    this.errorMessage,
    this.deliveredAt,
  });

  final int id;
  final String channel;
  final String recipient;
  final String subject;
  final String status;
  final String? errorMessage;
  final DateTime createdAt;
  final DateTime? deliveredAt;

  factory NotificationItem.fromJson(Map<String, dynamic> json) {
    return NotificationItem(
      id: json['id'] as int,
      channel: json['channel'] as String,
      recipient: json['recipient'] as String,
      subject: json['subject'] as String,
      status: json['status'] as String,
      errorMessage: json['error_message'] as String?,
      createdAt: DateTime.parse(json['created_at'] as String),
      deliveredAt: json['delivered_at'] == null ? null : DateTime.parse(json['delivered_at'] as String),
    );
  }
}
