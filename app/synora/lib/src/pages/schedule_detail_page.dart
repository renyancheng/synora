import 'package:flutter/material.dart';
import 'package:flutter_markdown/flutter_markdown.dart';

import '../app_controller.dart';
import '../date_utils.dart';
import '../models.dart';
import '../strings.dart';
import 'event_date_time_field.dart';

class ScheduleDetailPage extends StatefulWidget {
  const ScheduleDetailPage({
    super.key,
    required this.controller,
    required this.item,
  });

  final AppController controller;
  final ScheduleItem item;

  @override
  State<ScheduleDetailPage> createState() => _ScheduleDetailPageState();
}

class _ScheduleDetailPageState extends State<ScheduleDetailPage> {
  late ScheduleItem _item;
  late TextEditingController _titleController;
  late TextEditingController _locationController;
  late TextEditingController _detailsController;
  late bool _isAllDay;
  late DateTime _startAt;
  late DateTime _endAt;
  late Duration _defaultDuration;
  bool _editing = false;
  bool _saving = false;

  @override
  void initState() {
    super.initState();
    _item = widget.item;
    _titleController = TextEditingController();
    _locationController = TextEditingController();
    _detailsController = TextEditingController();
    _syncEditors();
  }

  @override
  void dispose() {
    _titleController.dispose();
    _locationController.dispose();
    _detailsController.dispose();
    super.dispose();
  }

  void _syncEditors() {
    _titleController.text = _item.title;
    _locationController.text = _item.location ?? '';
    _detailsController.text = _item.details;
    _isAllDay = _item.isAllDay;
    _startAt = _item.start.dateTime.toLocal();
    _endAt = _item.end.dateTime.toLocal();
    final duration = _endAt.difference(_startAt);
    _defaultDuration = duration.isNegative || duration.inMinutes < 1
        ? const Duration(hours: 1)
        : duration;
  }

  void _toggleEdit() {
    if (_saving) {
      return;
    }
    setState(() {
      _editing = !_editing;
      if (!_editing) {
        _titleController.text = _item.title;
        _locationController.text = _item.location ?? '';
        _detailsController.text = _item.details;
        _isAllDay = _item.isAllDay;
        _startAt = _item.start.dateTime.toLocal();
        _endAt = _item.end.dateTime.toLocal();
      }
    });
  }

  Future<void> _delete() async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text(AppStrings.deleteScheduleTitle),
        content: const Text(AppStrings.deleteScheduleMessage),
        actions: <Widget>[
          TextButton(
            onPressed: () => Navigator.of(context).pop(false),
            child: const Text(AppStrings.cancel),
          ),
          FilledButton(
            onPressed: () => Navigator.of(context).pop(true),
            child: const Text(AppStrings.delete),
          ),
        ],
      ),
    );
    if (confirmed != true) {
      return;
    }
    await widget.controller.deleteScheduleItem(_item.id);
    if (!mounted) {
      return;
    }
    ScaffoldMessenger.of(
      context,
    ).showSnackBar(const SnackBar(content: Text(AppStrings.deleteDone)));
    Navigator.of(context).pop(true);
  }

  ScheduleDraft _buildDraft() {
    final normalizedStart = _isAllDay
        ? DateTime(_startAt.year, _startAt.month, _startAt.day)
        : _startAt;
    final normalizedEnd = _isAllDay
        ? DateTime(_endAt.year, _endAt.month, _endAt.day)
        : _endAt;
    return ScheduleDraft(
      title: _titleController.text.trim(),
      location: _locationController.text.trim().isEmpty
          ? null
          : _locationController.text.trim(),
      details: _detailsController.text.trim(),
      sourceText: _item.sourceText,
      isAllDay: _isAllDay,
      start: EventDateTimeValue(
        dateTime: normalizedStart,
        timeZone: _item.start.timeZone,
      ),
      end: EventDateTimeValue(
        dateTime: normalizedEnd,
        timeZone: _item.end.timeZone,
      ),
      recurrence: List<String>.from(_item.recurrence),
      sourceAttachmentIds: List<int>.from(_item.sourceAttachmentIds),
      parseConfidence: _item.parseConfidence,
      evidenceDigest: const <String>[],
    );
  }

  Future<void> _save() async {
    final title = _titleController.text.trim();
    if (title.isEmpty) {
      _showMessage(AppStrings.titleRequired);
      return;
    }
    if (_endAt.isBefore(_startAt)) {
      _showMessage(AppStrings.endBeforeStart);
      return;
    }
    setState(() => _saving = true);
    try {
      final preview = await widget.controller.previewScheduleEdit(
        scheduleId: _item.id,
        draft: _buildDraft(),
      );
      if (!mounted) {
        return;
      }
      final confirmed = await showDialog<bool>(
        context: context,
        builder: (context) => AlertDialog(
          title: const Text(AppStrings.confirmSave),
          content: SingleChildScrollView(
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: <Widget>[
                Text('${AppStrings.titleField}：${preview.draft.title}'),
                const SizedBox(height: 8),
                Text(
                  '${AppStrings.startField}：${formatEventRange(start: preview.draft.start, end: preview.draft.end, isAllDay: preview.draft.isAllDay)}',
                ),
                const SizedBox(height: 8),
                Text(
                  '${AppStrings.riskLevelField}：${AppStrings.riskLevelLabel(preview.riskLevel)}',
                ),
                if (preview.conflictItems.isNotEmpty) ...<Widget>[
                  const SizedBox(height: 8),
                  Text(
                    '${AppStrings.conflictItemsField}：${preview.conflictItems.map((item) => item.title).join(' / ')}',
                  ),
                ],
              ],
            ),
          ),
          actions: <Widget>[
            TextButton(
              onPressed: () => Navigator.of(context).pop(false),
              child: const Text(AppStrings.cancel),
            ),
            FilledButton(
              onPressed: () => Navigator.of(context).pop(true),
              child: const Text(AppStrings.confirmSave),
            ),
          ],
        ),
      );
      if (confirmed != true) {
        return;
      }
      final updated = await widget.controller.confirmScheduleEdit(
        scheduleId: _item.id,
        approvalToken: preview.approval.approvalToken,
        draft: preview.draft,
      );
      if (!mounted) {
        return;
      }
      setState(() {
        _item = updated;
        _editing = false;
        _syncEditors();
      });
      _showMessage(AppStrings.saveSuccess);
    } catch (error) {
      _showMessage(error.toString());
    } finally {
      if (mounted) {
        setState(() => _saving = false);
      }
    }
  }

  void _showMessage(String message) {
    ScaffoldMessenger.of(
      context,
    ).showSnackBar(SnackBar(content: Text(message)));
  }

  void _updateStart(DateTime? value) {
    if (value == null) {
      return;
    }
    setState(() {
      _startAt = value;
      if (_endAt.isBefore(_startAt) || _endAt.isAtSameMomentAs(_startAt)) {
        _endAt = _isAllDay ? _startAt : _startAt.add(_defaultDuration);
      }
    });
  }

  void _updateEnd(DateTime? value) {
    if (value == null) {
      return;
    }
    setState(() {
      _endAt = value;
      if (_endAt.isBefore(_startAt)) {
        _endAt = _isAllDay ? _startAt : _startAt.add(_defaultDuration);
      }
    });
  }

  Widget _buildReadOnlyBody() {
    return ListView(
      padding: const EdgeInsets.all(20),
      children: <Widget>[
        Text(_item.title, style: Theme.of(context).textTheme.headlineSmall),
        const SizedBox(height: 20),
        _DetailLine(
          label: AppStrings.startField,
          value: formatEventRange(
            start: _item.start,
            end: _item.end,
            isAllDay: _item.isAllDay,
          ),
        ),
        _DetailLine(
          label: AppStrings.locationField,
          value: (_item.location?.trim().isNotEmpty ?? false)
              ? _item.location!
              : AppStrings.noContent,
        ),
        _DetailLine(
          label: AppStrings.reminderField,
          value: formatReminderOffsets(_item.reminderOffsetsMinutes),
        ),
        _DetailLine(
          label: AppStrings.recurrenceField,
          value: formatRecurrence(_item.recurrence),
        ),
        _DetailLine(
          label: AppStrings.timeZoneField,
          value: _item.start.timeZone,
        ),
        _DetailLine(
          label: AppStrings.parseConfidenceField,
          value: '${(_item.parseConfidence * 100).toStringAsFixed(0)}%',
        ),
        _DetailLine(
          label: AppStrings.sourceTextField,
          value: _item.sourceText.trim().isEmpty
              ? AppStrings.noContent
              : _item.sourceText,
          markdown: false,
          selectable: true,
        ),
        _DetailLine(
          label: AppStrings.detailsField,
          value: _item.details.trim().isEmpty
              ? AppStrings.noContent
              : _item.details,
          markdown: true,
        ),
      ],
    );
  }

  Widget _buildEditBody() {
    return ListView(
      padding: const EdgeInsets.all(20),
      children: <Widget>[
        TextField(
          controller: _titleController,
          decoration: const InputDecoration(labelText: AppStrings.titleField),
        ),
        const SizedBox(height: 16),
        SwitchListTile(
          contentPadding: EdgeInsets.zero,
          title: const Text(AppStrings.allDayField),
          value: _isAllDay,
          onChanged: (value) {
            setState(() {
              _isAllDay = value;
            });
          },
        ),
        const SizedBox(height: 8),
        EventDateTimeField(
          label: AppStrings.startField,
          value: _startAt,
          isAllDay: _isAllDay,
          enabled: !_saving,
          onChanged: _updateStart,
        ),
        const SizedBox(height: 16),
        EventDateTimeField(
          label: AppStrings.endField,
          value: _endAt,
          isAllDay: _isAllDay,
          enabled: !_saving,
          onChanged: _updateEnd,
        ),
        const SizedBox(height: 16),
        TextField(
          controller: _locationController,
          decoration: const InputDecoration(
            labelText: AppStrings.locationField,
          ),
        ),
        const SizedBox(height: 16),
        TextField(
          controller: _detailsController,
          minLines: 3,
          maxLines: 6,
          decoration: const InputDecoration(labelText: AppStrings.detailsField),
        ),
        const SizedBox(height: 16),
        _DetailLine(
          label: AppStrings.sourceTextField,
          value: _item.sourceText.trim().isEmpty
              ? AppStrings.noContent
              : _item.sourceText,
          markdown: false,
          selectable: true,
        ),
        _DetailLine(
          label: AppStrings.reminderField,
          value: formatReminderOffsets(_item.reminderOffsetsMinutes),
        ),
        _DetailLine(
          label: AppStrings.recurrenceField,
          value: formatRecurrence(_item.recurrence),
        ),
        _DetailLine(
          label: AppStrings.timeZoneField,
          value: _item.start.timeZone,
        ),
        _DetailLine(
          label: AppStrings.parseConfidenceField,
          value: '${(_item.parseConfidence * 100).toStringAsFixed(0)}%',
        ),
      ],
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: Text(_editing ? AppStrings.editSchedule : _item.title),
        actions: <Widget>[
          if (_editing)
            TextButton(
              onPressed: _saving ? null : _save,
              child: Text(
                _saving ? AppStrings.loading : AppStrings.saveChanges,
              ),
            )
          else
            IconButton(
              onPressed: _toggleEdit,
              icon: const Icon(Icons.edit_outlined),
              tooltip: AppStrings.edit,
            ),
          PopupMenuButton<String>(
            onSelected: (value) {
              if (value == 'delete') {
                _delete();
              }
            },
            itemBuilder: (context) => const <PopupMenuEntry<String>>[
              PopupMenuItem<String>(
                value: 'delete',
                child: ListTile(
                  contentPadding: EdgeInsets.zero,
                  leading: Icon(Icons.delete_outline),
                  title: Text(AppStrings.delete),
                ),
              ),
            ],
          ),
        ],
      ),
      body: _editing ? _buildEditBody() : _buildReadOnlyBody(),
    );
  }
}

class _DetailLine extends StatelessWidget {
  const _DetailLine({
    required this.label,
    required this.value,
    this.markdown = false,
    this.selectable = false,
  });

  final String label;
  final String value;
  final bool markdown;
  final bool selectable;

  @override
  Widget build(BuildContext context) {
    Widget body;
    if (markdown) {
      body = MarkdownBody(
        data: value,
        selectable: true,
        styleSheet: MarkdownStyleSheet.fromTheme(Theme.of(context)),
      );
    } else if (selectable) {
      body = SelectableText(value);
    } else {
      body = Text(value);
    }
    return Padding(
      padding: const EdgeInsets.only(bottom: 16),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Text(label, style: Theme.of(context).textTheme.labelMedium),
          const SizedBox(height: 6),
          body,
        ],
      ),
    );
  }
}
