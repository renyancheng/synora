import 'package:flutter/foundation.dart';

import 'api_client.dart';
import 'models.dart';

class AppController extends ChangeNotifier {
  final ApiClient _apiClient = ApiClient();

  SessionInfo? _session;
  bool _loading = false;
  String? _lastError;
  List<ScheduleItem> _schedules = <ScheduleItem>[];
  List<QuickNoteItem> _quickNotes = <QuickNoteItem>[];
  List<NotificationItem> _notifications = <NotificationItem>[];

  SessionInfo? get session => _session;
  bool get isAuthenticated => _session != null;
  bool get isLoading => _loading;
  String? get lastError => _lastError;
  List<ScheduleItem> get schedules => List<ScheduleItem>.unmodifiable(_schedules);
  List<QuickNoteItem> get quickNotes => List<QuickNoteItem>.unmodifiable(_quickNotes);
  List<NotificationItem> get notifications => List<NotificationItem>.unmodifiable(_notifications);

  Future<void> login(String email, String password) async {
    _setLoading(true);
    try {
      _session = await _apiClient.login(email, password);
      _lastError = null;
      await loadDashboard();
    } catch (error) {
      _lastError = error.toString();
      rethrow;
    } finally {
      _setLoading(false);
    }
  }

  Future<void> loadDashboard() async {
    if (!isAuthenticated) {
      return;
    }
    _setLoading(true);
    try {
      final results = await Future.wait<dynamic>([
        _apiClient.fetchSchedules(),
        _apiClient.fetchQuickNotes(),
        _apiClient.fetchNotifications(),
      ]);
      _schedules = results[0] as List<ScheduleItem>;
      _quickNotes = results[1] as List<QuickNoteItem>;
      _notifications = results[2] as List<NotificationItem>;
      _lastError = null;
    } catch (error) {
      _lastError = error.toString();
      rethrow;
    } finally {
      _setLoading(false);
    }
  }

  Future<UploadedAttachment> uploadAttachment(
    InputSourceType sourceType,
    LocalAttachmentData attachment,
  ) {
    return _apiClient.uploadAttachment(sourceType, attachment);
  }

  Future<ScheduleDraftResult> createScheduleDraft({
    required InputSourceType sourceType,
    required String textContent,
    required List<int> attachmentIds,
  }) {
    return _apiClient.createScheduleDraft(
      sourceType: sourceType,
      textContent: textContent,
      attachmentIds: attachmentIds,
    );
  }

  Future<ConflictCheckResult> checkScheduleConflicts(
    ScheduleDraft draft,
    String draftHash,
  ) {
    return _apiClient.checkScheduleConflicts(draft, draftHash);
  }

  Future<ScheduleConfirmResult> confirmSchedule(String approvalToken, ScheduleDraft draft) async {
    final result = await _apiClient.confirmSchedule(approvalToken, draft);
    await loadDashboard();
    return result;
  }

  Future<QuickNoteDraftPreview> createQuickNoteDraft({
    required InputSourceType sourceType,
    required String content,
    required List<String> tags,
    required List<int> attachmentIds,
  }) {
    return _apiClient.createQuickNoteDraft(
      sourceType: sourceType,
      content: content,
      tags: tags,
      attachmentIds: attachmentIds,
    );
  }

  Future<QuickNoteSaveResult> confirmQuickNote({
    required String content,
    required List<String> tags,
    required InputSourceType sourceType,
    required List<int> attachmentIds,
    required String approvalToken,
  }) async {
    final result = await _apiClient.confirmQuickNote(
      content: content,
      tags: tags,
      sourceType: sourceType,
      attachmentIds: attachmentIds,
      approvalToken: approvalToken,
    );
    await loadDashboard();
    return result;
  }

  void logout() {
    _session = null;
    _apiClient.setAccessToken(null);
    _schedules = <ScheduleItem>[];
    _quickNotes = <QuickNoteItem>[];
    _notifications = <NotificationItem>[];
    _lastError = null;
    notifyListeners();
  }

  void _setLoading(bool value) {
    _loading = value;
    notifyListeners();
  }
}
