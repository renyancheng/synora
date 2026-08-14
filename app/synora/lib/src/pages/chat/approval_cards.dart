import 'package:flutter/material.dart';

import '../../date_utils.dart';
import '../../models.dart';
import '../../strings.dart';
import '../../tag_palette.dart';
import '../event_date_time_field.dart';

/// 结构化消息卡片分发：日程草稿 / 冲突检查 / 速记预览。
class StructuredMessageCard extends StatelessWidget {
  const StructuredMessageCard({
    super.key,
    required this.message,
    required this.onAction,
  });

  final ConversationMessageItem message;
  final Future<void> Function(String action, {Map<String, dynamic> payload})
  onAction;

  @override
  Widget build(BuildContext context) {
    switch (message.messageType) {
      case 'schedule_draft_card':
        return ScheduleDraftCard(message: message, onAction: onAction);
      case 'conflict_card':
        return ConflictCard(message: message);
      case 'quick_note_preview_card':
        return QuickNotePreviewCard(message: message, onAction: onAction);
      case 'reasoning_step':
        // 推理步骤明细不再渲染：实时生成只展示气泡上方的 plan 行，
        // 持久化 reasoning_step 消息保留在后端数据中但不做 UI 展示。
        return const SizedBox.shrink();
      default:
        return const SizedBox.shrink();
    }
  }
}

class ScheduleDraftCard extends StatefulWidget {
  const ScheduleDraftCard({
    super.key,
    required this.message,
    required this.onAction,
  });

  final ConversationMessageItem message;
  final Future<void> Function(String action, {Map<String, dynamic> payload})
  onAction;

  @override
  State<ScheduleDraftCard> createState() => _ScheduleDraftCardState();
}

class _ScheduleDraftCardState extends State<ScheduleDraftCard> {
  late final TextEditingController _titleController;
  late final TextEditingController _locationController;
  late final TextEditingController _detailsController;
  late DateTime? _startValue;
  late DateTime? _endValue;
  late String _reminderPreset;
  bool _busy = false;

  Map<String, dynamic> get _payload => widget.message.structuredPayload;
  Map<String, dynamic> get _draft =>
      (_payload['draft'] as Map<String, dynamic>? ?? <String, dynamic>{});

  @override
  void initState() {
    super.initState();
    _titleController = TextEditingController(
      text: _draft['title'] as String? ?? '',
    );
    _locationController = TextEditingController(
      text: _draft['location'] as String? ?? '',
    );
    _detailsController = TextEditingController(
      text: _draft['details'] as String? ?? '',
    );
    final start = _draft['start'] as Map<String, dynamic>?;
    final end = _draft['end'] as Map<String, dynamic>?;
    _startValue = start == null
        ? null
        : DateTime.tryParse(start['dateTime'] as String? ?? '')?.toLocal();
    _endValue = end == null
        ? null
        : DateTime.tryParse(end['dateTime'] as String? ?? '')?.toLocal();
    _reminderPreset =
        _draft['reminder_preset'] as String? ?? 'previous_day_1700';
  }

  @override
  void dispose() {
    _titleController.dispose();
    _locationController.dispose();
    _detailsController.dispose();
    super.dispose();
  }

  Future<void> _submitMissingFields() async {
    final parsedStart = _startValue;
    final parsedEnd = _endValue;
    setState(() => _busy = true);
    try {
      await widget.onAction(
        'submit_missing_fields',
        payload: <String, dynamic>{
          'title': _titleController.text.trim(),
          'start_at': parsedStart?.toLocal().toIso8601String(),
          'end_at': parsedEnd?.toLocal().toIso8601String(),
          'location': _locationController.text.trim(),
          'details': _detailsController.text.trim(),
          'reminder_preset': _reminderPreset,
        },
      );
    } finally {
      if (mounted) {
        setState(() => _busy = false);
      }
    }
  }

  Future<void> _confirm() async {
    setState(() => _busy = true);
    try {
      await widget.onAction(
        'confirm_schedule_draft',
        payload: <String, dynamic>{'reminder_preset': _reminderPreset},
      );
    } finally {
      if (mounted) {
        setState(() => _busy = false);
      }
    }
  }

  Future<void> _dismiss() async {
    setState(() => _busy = true);
    try {
      await widget.onAction('dismiss_pending_action');
    } finally {
      if (mounted) {
        setState(() => _busy = false);
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final missingFields =
        (_payload['missing_fields'] as List<dynamic>? ?? <dynamic>[])
            .cast<String>();
    final ambiguityFlags =
        (_payload['ambiguity_flags'] as List<dynamic>? ?? <dynamic>[])
            .cast<String>();
    final evidenceDigest =
        (_payload['evidence_digest'] as List<dynamic>? ?? <dynamic>[])
            .cast<String>();
    final parseConfidence =
        (_payload['parse_confidence'] as num?)?.toDouble() ?? 0;
    final stage = _payload['stage'] as String? ?? 'approval_pending';
    final isActionable = _payload['is_actionable'] as bool? ?? false;
    final lifecycle = _payload['lifecycle_status'] as String? ?? stage;
    final recurrence = (_draft['recurrence'] as List<dynamic>? ?? <dynamic>[])
        .cast<String>();
    final isEditing = stage == 'needs_input' && isActionable;
    final terminalSummary = _payload['terminal_summary'] as String?;

    return _CardShell(
      title: AppStrings.scheduleDraft,
      lifecycle: lifecycle,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          TextField(
            controller: _titleController,
            enabled: isEditing && !_busy,
            decoration: const InputDecoration(labelText: AppStrings.titleField),
          ),
          const SizedBox(height: 12),
          EventDateTimeField(
            label: AppStrings.startField,
            value: _startValue,
            isAllDay: false,
            enabled: isEditing && !_busy,
            onChanged: (value) {
              if (value == null) {
                return;
              }
              setState(() {
                _startValue = value;
                if (_endValue == null || !_endValue!.isAfter(value)) {
                  _endValue = value.add(const Duration(hours: 1));
                }
              });
            },
          ),
          const SizedBox(height: 12),
          EventDateTimeField(
            label: AppStrings.endField,
            value: _endValue,
            isAllDay: false,
            enabled: isEditing && !_busy,
            onChanged: (value) {
              if (value == null) {
                return;
              }
              setState(() {
                _endValue = value.isAfter(_startValue ?? value)
                    ? value
                    : (_startValue ?? value).add(const Duration(hours: 1));
              });
            },
          ),
          const SizedBox(height: 12),
          TextField(
            controller: _locationController,
            enabled: isEditing && !_busy,
            decoration: const InputDecoration(
              labelText: AppStrings.locationField,
            ),
          ),
          const SizedBox(height: 12),
          TextField(
            controller: _detailsController,
            enabled: isEditing && !_busy,
            minLines: 2,
            maxLines: 4,
            decoration: const InputDecoration(
              labelText: AppStrings.detailsField,
            ),
          ),
          const SizedBox(height: 12),
          DropdownButtonFormField<String>(
            initialValue: _reminderPreset,
            decoration: const InputDecoration(
              labelText: AppStrings.reminderField,
            ),
            items: reminderPresetOptions
                .map(
                  (item) => DropdownMenuItem<String>(
                    value: item,
                    child: Text(formatReminderPreset(item)),
                  ),
                )
                .toList(),
            onChanged: isActionable && !_busy
                ? (value) {
                    if (value == null) {
                      return;
                    }
                    setState(() => _reminderPreset = value);
                  }
                : null,
          ),
          const SizedBox(height: 12),
          _SectionChips(
            title: AppStrings.missingFieldsField,
            values: missingFields.map(AppStrings.missingFieldLabel).toList(),
          ),
          _SectionChips(
            title: AppStrings.ambiguityField,
            values: ambiguityFlags.map(AppStrings.ambiguityLabel).toList(),
          ),
          _SectionList(title: AppStrings.evidenceField, values: evidenceDigest),
          _SectionList(
            title: AppStrings.recurrenceField,
            values: <String>[formatRecurrence(recurrence)],
          ),
          if (terminalSummary != null &&
              terminalSummary.trim().isNotEmpty) ...<Widget>[
            Text(terminalSummary),
            const SizedBox(height: 8),
          ],
          Text(
            '${AppStrings.reminderField}：${formatReminderPreset(_reminderPreset)}',
          ),
          const SizedBox(height: 8),
          Text(
            '${AppStrings.parseConfidenceField}：${(parseConfidence * 100).toStringAsFixed(0)}%',
          ),
          if (isActionable) ...<Widget>[
            const SizedBox(height: 16),
            Row(
              children: <Widget>[
                Expanded(
                  child: OutlinedButton(
                    onPressed: _busy ? null : _dismiss,
                    child: const Text(AppStrings.cancelPendingAction),
                  ),
                ),
                const SizedBox(width: 12),
                Expanded(
                  child: FilledButton(
                    onPressed: _busy
                        ? null
                        : (isEditing ? _submitMissingFields : _confirm),
                    child: Text(
                      _busy
                          ? AppStrings.loading
                          : (isEditing
                                ? AppStrings.submitMissingFields
                                : AppStrings.confirmSave),
                    ),
                  ),
                ),
              ],
            ),
          ],
        ],
      ),
    );
  }
}

class ConflictCard extends StatelessWidget {
  const ConflictCard({super.key, required this.message});

  final ConversationMessageItem message;

  @override
  Widget build(BuildContext context) {
    final payload = message.structuredPayload;
    final conflicts =
        (payload['conflict_items'] as List<dynamic>? ?? <dynamic>[])
            .map((item) => Map<String, dynamic>.from(item as Map))
            .toList();
    final suggestions =
        (payload['suggestions'] as List<dynamic>? ?? <dynamic>[])
            .map((item) => Map<String, dynamic>.from(item as Map))
            .toList();
    final riskLevel = payload['risk_level'] as String? ?? 'low';
    final lifecycle =
        payload['lifecycle_status'] as String? ?? 'conflict_review';

    return _CardShell(
      title: AppStrings.conflictCheck,
      lifecycle: lifecycle,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Text(
            '${AppStrings.riskLevelField}：${AppStrings.riskLevelLabel(riskLevel)}',
          ),
          const SizedBox(height: 12),
          _SectionList(
            title: AppStrings.conflictItemsField,
            values: conflicts.isEmpty
                ? const <String>['未发现冲突']
                : conflicts.map((item) {
                    final start = EventDateTimeValue.fromJson(
                      item['start'] as Map<String, dynamic>,
                    );
                    final end = EventDateTimeValue.fromJson(
                      item['end'] as Map<String, dynamic>,
                    );
                    return '${item['title']}：${formatEventRange(start: start, end: end, isAllDay: false)}';
                  }).toList(),
          ),
          _SectionList(
            title: AppStrings.suggestionsField,
            values: suggestions.isEmpty
                ? const <String>['暂无建议时段']
                : suggestions.map((item) {
                    final start = EventDateTimeValue.fromJson(
                      item['start'] as Map<String, dynamic>,
                    );
                    final end = EventDateTimeValue.fromJson(
                      item['end'] as Map<String, dynamic>,
                    );
                    return '${item['label']}：${formatEventRange(start: start, end: end, isAllDay: false)}';
                  }).toList(),
          ),
        ],
      ),
    );
  }
}

class QuickNotePreviewCard extends StatefulWidget {
  const QuickNotePreviewCard({
    super.key,
    required this.message,
    required this.onAction,
  });

  final ConversationMessageItem message;
  final Future<void> Function(String action, {Map<String, dynamic> payload})
  onAction;

  @override
  State<QuickNotePreviewCard> createState() => _QuickNotePreviewCardState();
}

class _QuickNotePreviewCardState extends State<QuickNotePreviewCard> {
  bool _busy = false;

  Future<void> _confirm() async {
    setState(() => _busy = true);
    try {
      await widget.onAction('confirm_quick_note');
    } finally {
      if (mounted) {
        setState(() => _busy = false);
      }
    }
  }

  Future<void> _dismiss() async {
    setState(() => _busy = true);
    try {
      await widget.onAction('dismiss_pending_action');
    } finally {
      if (mounted) {
        setState(() => _busy = false);
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final payload = widget.message.structuredPayload;
    final tags = (payload['preview_tags'] as List<dynamic>? ?? <dynamic>[])
        .cast<String>();
    final evidenceDigest =
        (payload['evidence_digest'] as List<dynamic>? ?? <dynamic>[])
            .cast<String>();
    final lifecycle =
        payload['lifecycle_status'] as String? ?? 'approval_pending';
    final isActionable = payload['is_actionable'] as bool? ?? false;
    final terminalSummary = payload['terminal_summary'] as String?;

    return _CardShell(
      title: AppStrings.quickNotePreview,
      lifecycle: lifecycle,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Text(payload['normalized_content'] as String? ?? ''),
          const SizedBox(height: 12),
          if (terminalSummary != null &&
              terminalSummary.trim().isNotEmpty) ...<Widget>[
            Text(terminalSummary),
            const SizedBox(height: 12),
          ],
          _SectionChips(
            title: AppStrings.tagsField,
            values: tags,
            colored: true,
          ),
          _SectionList(title: AppStrings.evidenceField, values: evidenceDigest),
          if (isActionable) ...<Widget>[
            const SizedBox(height: 16),
            Row(
              children: <Widget>[
                Expanded(
                  child: OutlinedButton(
                    onPressed: _busy ? null : _dismiss,
                    child: const Text(AppStrings.cancelPendingAction),
                  ),
                ),
                const SizedBox(width: 12),
                Expanded(
                  child: FilledButton(
                    onPressed: _busy ? null : _confirm,
                    child: Text(
                      _busy ? AppStrings.loading : AppStrings.confirmSave,
                    ),
                  ),
                ),
              ],
            ),
          ],
        ],
      ),
    );
  }
}

class _CardShell extends StatelessWidget {
  const _CardShell({
    required this.title,
    required this.lifecycle,
    required this.child,
  });

  final String title;
  final String lifecycle;
  final Widget child;

  @override
  Widget build(BuildContext context) {
    return Card(
      color: switch (lifecycle) {
        'confirmed' => const Color(0xFFF0FAF3),
        'cancelled' => const Color(0xFFFFF6EF),
        'superseded' => const Color(0xFFF4F6F8),
        _ => null,
      },
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            Row(
              children: <Widget>[
                Expanded(
                  child: Text(
                    title,
                    style: Theme.of(context).textTheme.titleMedium,
                  ),
                ),
                Chip(label: Text(AppStrings.lifecycleLabel(lifecycle))),
              ],
            ),
            const SizedBox(height: 12),
            child,
          ],
        ),
      ),
    );
  }
}

class _SectionChips extends StatelessWidget {
  const _SectionChips({
    required this.title,
    required this.values,
    this.colored = false,
  });

  final String title;
  final List<String> values;
  final bool colored;

  @override
  Widget build(BuildContext context) {
    if (values.isEmpty) {
      return const SizedBox.shrink();
    }
    return Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Text(title, style: Theme.of(context).textTheme.labelLarge),
          const SizedBox(height: 8),
          Wrap(
            spacing: 8,
            runSpacing: 8,
            children: values.map((item) {
              if (!colored) {
                return Chip(label: Text(item));
              }
              final colors = TagPalette.resolve(item);
              return Chip(
                label: Text(item, style: TextStyle(color: colors.foreground)),
                backgroundColor: colors.background,
                side: BorderSide(color: colors.border),
              );
            }).toList(),
          ),
        ],
      ),
    );
  }
}

class _SectionList extends StatelessWidget {
  const _SectionList({required this.title, required this.values});

  final String title;
  final List<String> values;

  @override
  Widget build(BuildContext context) {
    if (values.isEmpty) {
      return const SizedBox.shrink();
    }
    return Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Text(title, style: Theme.of(context).textTheme.labelLarge),
          const SizedBox(height: 8),
          ...values.map(
            (item) => Padding(
              padding: const EdgeInsets.only(bottom: 4),
              child: Text('• $item'),
            ),
          ),
        ],
      ),
    );
  }
}
