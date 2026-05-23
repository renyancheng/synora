import 'package:flutter/material.dart';

import '../app_controller.dart';
import '../date_utils.dart';
import '../models.dart';

class ScheduleDraftPage extends StatefulWidget {
  const ScheduleDraftPage({super.key, required this.controller});

  final AppController controller;

  @override
  State<ScheduleDraftPage> createState() => _ScheduleDraftPageState();
}

class _ScheduleDraftPageState extends State<ScheduleDraftPage> {
  late final TextEditingController _inputController;
  late final TextEditingController _titleController;
  late final TextEditingController _detailsController;
  late final TextEditingController _locationController;
  late final TextEditingController _scheduledAtController;

  ScheduleDraftResult? _draftResult;
  ConflictCheckResult? _conflictResult;
  bool _submitting = false;

  @override
  void initState() {
    super.initState();
    _inputController = TextEditingController();
    _titleController = TextEditingController();
    _detailsController = TextEditingController();
    _locationController = TextEditingController();
    _scheduledAtController = TextEditingController();
  }

  @override
  void dispose() {
    _inputController.dispose();
    _titleController.dispose();
    _detailsController.dispose();
    _locationController.dispose();
    _scheduledAtController.dispose();
    super.dispose();
  }

  Future<void> _parseDraft() async {
    if (_inputController.text.trim().isEmpty) {
      _showMessage('Enter a schedule note first.');
      return;
    }
    setState(() => _submitting = true);
    try {
      final result = await widget.controller.createScheduleDraft(
        _inputController.text.trim(),
      );
      _titleController.text = result.draft.title;
      _detailsController.text = result.draft.details;
      _locationController.text = result.draft.location ?? '';
      _scheduledAtController.text = result.draft.scheduledAt == null
          ? ''
          : formatDateTime(result.draft.scheduledAt);
      setState(() {
        _draftResult = result;
        _conflictResult = null;
      });
    } catch (error) {
      _showMessage(error.toString());
    } finally {
      if (mounted) {
        setState(() => _submitting = false);
      }
    }
  }

  ScheduleDraft? _buildDraft() {
    final scheduledAt = parseEditableDateTime(_scheduledAtController.text);
    if (scheduledAt == null) {
      _showMessage('Use datetime format like 2026-05-25 14:30.');
      return null;
    }
    return ScheduleDraft(
      title: _titleController.text.trim().isEmpty
          ? 'Needs confirmation'
          : _titleController.text.trim(),
      location: _locationController.text.trim().isEmpty
          ? null
          : _locationController.text.trim(),
      details: _detailsController.text.trim().isEmpty
          ? _inputController.text.trim()
          : _detailsController.text.trim(),
      sourceText: _inputController.text.trim(),
      scheduledAt: scheduledAt,
      durationMinutes: 60,
      reminderAt: computeReminderAt(scheduledAt),
    );
  }

  Future<void> _checkConflicts() async {
    final draft = _buildDraft();
    if (draft == null) {
      return;
    }
    setState(() => _submitting = true);
    try {
      final result = await widget.controller.checkScheduleConflicts(
        draft,
        _draftResult?.draftHash ?? '',
      );
      setState(() => _conflictResult = result);
    } catch (error) {
      _showMessage(error.toString());
    } finally {
      if (mounted) {
        setState(() => _submitting = false);
      }
    }
  }

  Future<void> _confirmSchedule() async {
    final conflictResult = _conflictResult;
    if (conflictResult == null) {
      _showMessage('Run conflict checks before saving.');
      return;
    }
    final draft = _buildDraft();
    if (draft == null) {
      return;
    }
    setState(() => _submitting = true);
    try {
      await widget.controller.confirmSchedule(
        conflictResult.approval.approvalToken,
        draft,
      );
      if (!mounted) {
        return;
      }
      _showMessage('Schedule saved and reminder jobs created.');
      Navigator.of(context).pop(true);
    } catch (error) {
      _showMessage(error.toString());
    } finally {
      if (mounted) {
        setState(() => _submitting = false);
      }
    }
  }

  void _showMessage(String message) {
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(content: Text(message)),
    );
  }

  void _showAttachmentPlaceholder() {
    _showMessage(
      'Attachments are planned for the next phase. This MVP is text-first.',
    );
  }

  @override
  Widget build(BuildContext context) {
    final draftResult = _draftResult;
    final conflictResult = _conflictResult;
    return Scaffold(
      appBar: AppBar(title: const Text('New schedule')),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: <Widget>[
          TextField(
            controller: _inputController,
            maxLines: 6,
            decoration: InputDecoration(
              labelText: 'Schedule text',
              hintText:
                  'Example: Tomorrow 14:30 in the faculty room to discuss course Q&A planning',
              suffixIcon: IconButton(
                onPressed: _showAttachmentPlaceholder,
                icon: const Icon(Icons.attach_file),
                tooltip: 'Attachment placeholder',
              ),
            ),
          ),
          const SizedBox(height: 16),
          FilledButton(
            onPressed: _submitting ? null : _parseDraft,
            child: Text(_submitting ? 'Working...' : 'Parse draft'),
          ),
          if (draftResult != null) ...<Widget>[
            const SizedBox(height: 20),
            if (draftResult.missingFields.isNotEmpty)
              _InfoCard(
                title: 'Missing fields',
                child: Wrap(
                  spacing: 8,
                  children: draftResult.missingFields
                      .map((item) => Chip(label: Text(item)))
                      .toList(),
                ),
              ),
            if (draftResult.ambiguityFlags.isNotEmpty)
              _InfoCard(
                title: 'Parser hints',
                child: Wrap(
                  spacing: 8,
                  children: draftResult.ambiguityFlags
                      .map((item) => Chip(label: Text(item)))
                      .toList(),
                ),
              ),
            _InfoCard(
              title: 'Review draft',
              child: Column(
                children: <Widget>[
                  TextField(
                    controller: _titleController,
                    decoration: const InputDecoration(labelText: 'Title'),
                  ),
                  const SizedBox(height: 12),
                  TextField(
                    controller: _scheduledAtController,
                    decoration: const InputDecoration(
                      labelText: 'Scheduled at',
                      hintText: '2026-05-25 14:30',
                    ),
                  ),
                  const SizedBox(height: 12),
                  TextField(
                    controller: _locationController,
                    decoration: const InputDecoration(
                      labelText: 'Location (optional)',
                    ),
                  ),
                  const SizedBox(height: 12),
                  TextField(
                    controller: _detailsController,
                    minLines: 3,
                    maxLines: 5,
                    decoration: const InputDecoration(labelText: 'Details'),
                  ),
                  const SizedBox(height: 16),
                  FilledButton.tonal(
                    onPressed: _submitting ? null : _checkConflicts,
                    child: const Text('Check conflicts'),
                  ),
                ],
              ),
            ),
          ],
          if (conflictResult != null) ...<Widget>[
            const SizedBox(height: 16),
            _InfoCard(
              title: 'Conflict review',
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: <Widget>[
                  Text('Risk level: ${conflictResult.riskLevel}'),
                  const SizedBox(height: 12),
                  if (conflictResult.conflictItems.isEmpty)
                    const Text(
                      'No conflict detected. You can save this schedule now.',
                    )
                  else
                    ...conflictResult.conflictItems.map(
                      (item) => ListTile(
                        contentPadding: EdgeInsets.zero,
                        title: Text(item.title),
                        subtitle: Text(
                          '${formatDateTime(item.startsAt)} - ${formatDateTime(item.endsAt)}',
                        ),
                      ),
                    ),
                  if (conflictResult.suggestions.isNotEmpty) ...<Widget>[
                    const Divider(),
                    const Text('Suggestions'),
                    const SizedBox(height: 8),
                    ...conflictResult.suggestions.map(
                      (item) => Text(
                        '${item.label}: ${formatDateTime(item.candidateStart)}',
                      ),
                    ),
                  ],
                  const SizedBox(height: 16),
                  FilledButton(
                    onPressed: _submitting ? null : _confirmSchedule,
                    child: const Text('Approve and save'),
                  ),
                ],
              ),
            ),
          ],
        ],
      ),
    );
  }
}

class _InfoCard extends StatelessWidget {
  const _InfoCard({required this.title, required this.child});

  final String title;
  final Widget child;

  @override
  Widget build(BuildContext context) {
    return Card(
      margin: const EdgeInsets.only(bottom: 16),
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(20)),
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            Text(title, style: Theme.of(context).textTheme.titleMedium),
            const SizedBox(height: 12),
            child,
          ],
        ),
      ),
    );
  }
}
