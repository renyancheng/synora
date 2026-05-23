import 'package:flutter/material.dart';

import '../app_controller.dart';
import '../date_utils.dart';
import '../models.dart';

class ScheduleConfirmPage extends StatefulWidget {
  const ScheduleConfirmPage({
    super.key,
    required this.controller,
    required this.draftResult,
  });

  final AppController controller;
  final ScheduleDraftResult draftResult;

  @override
  State<ScheduleConfirmPage> createState() => _ScheduleConfirmPageState();
}

class _ScheduleConfirmPageState extends State<ScheduleConfirmPage> {
  late final TextEditingController _titleController;
  late final TextEditingController _detailsController;
  late final TextEditingController _locationController;
  late final TextEditingController _scheduledAtController;
  ConflictCheckResult? _conflictResult;
  bool _busy = false;

  @override
  void initState() {
    super.initState();
    final draft = widget.draftResult.draft;
    _titleController = TextEditingController(text: draft.title);
    _detailsController = TextEditingController(text: draft.details);
    _locationController = TextEditingController(text: draft.location ?? '');
    _scheduledAtController = TextEditingController(
      text: draft.scheduledAt == null ? '' : formatDateTime(draft.scheduledAt),
    );
  }

  @override
  void dispose() {
    _titleController.dispose();
    _detailsController.dispose();
    _locationController.dispose();
    _scheduledAtController.dispose();
    super.dispose();
  }

  ScheduleDraft? _buildDraft() {
    final scheduledAt = parseEditableDateTime(_scheduledAtController.text);
    if (scheduledAt == null) {
      _showMessage('请按“2026-05-25 14:30”或“2026年05月25日 14:30”填写时间。');
      return null;
    }
    return widget.draftResult.draft.copyWith(
      title: _titleController.text.trim().isEmpty ? '待确认事项' : _titleController.text.trim(),
      location: _locationController.text.trim().isEmpty ? null : _locationController.text.trim(),
      details: _detailsController.text.trim().isEmpty ? widget.draftResult.draft.details : _detailsController.text.trim(),
      scheduledAt: scheduledAt,
    );
  }

  Future<void> _checkConflicts() async {
    final draft = _buildDraft();
    if (draft == null) {
      return;
    }
    setState(() => _busy = true);
    try {
      final result = await widget.controller.checkScheduleConflicts(
        draft,
        widget.draftResult.draftHash,
      );
      setState(() => _conflictResult = result);
    } catch (error) {
      _showMessage(error.toString());
    } finally {
      if (mounted) {
        setState(() => _busy = false);
      }
    }
  }

  Future<void> _confirmSchedule() async {
    final conflictResult = _conflictResult;
    if (conflictResult == null) {
      _showMessage('请先执行冲突检测。');
      return;
    }
    final draft = _buildDraft();
    if (draft == null) {
      return;
    }
    setState(() => _busy = true);
    try {
      await widget.controller.confirmSchedule(conflictResult.approval.approvalToken, draft);
      if (!mounted) {
        return;
      }
      _showMessage('日程已保存，提醒任务已创建。');
      Navigator.of(context).pop();
    } catch (error) {
      _showMessage(error.toString());
    } finally {
      if (mounted) {
        setState(() => _busy = false);
      }
    }
  }

  void _showMessage(String message) {
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(message)));
  }

  @override
  Widget build(BuildContext context) {
    final draftResult = widget.draftResult;
    final conflictResult = _conflictResult;
    return Scaffold(
      appBar: AppBar(title: const Text('日程确认')),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: <Widget>[
          if (draftResult.missingFields.isNotEmpty)
            _InfoCard(
              title: '待补充字段',
              child: Wrap(
                spacing: 8,
                children: draftResult.missingFields.map((item) => Chip(label: Text(item))).toList(),
              ),
            ),
          if (draftResult.ambiguityFlags.isNotEmpty)
            _InfoCard(
              title: '解析歧义提示',
              child: Wrap(
                spacing: 8,
                children: draftResult.ambiguityFlags.map((item) => Chip(label: Text(item))).toList(),
              ),
            ),
          _InfoCard(
            title: '提取依据',
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: draftResult.evidenceDigest.map((item) => Text('• $item')).toList(),
            ),
          ),
          _InfoCard(
            title: '确认并补充信息',
            child: Column(
              children: <Widget>[
                TextField(
                  controller: _titleController,
                  decoration: const InputDecoration(labelText: '事项标题'),
                ),
                const SizedBox(height: 12),
                TextField(
                  controller: _scheduledAtController,
                  decoration: const InputDecoration(
                    labelText: '发生时间',
                    hintText: '2026-05-25 14:30',
                  ),
                ),
                const SizedBox(height: 12),
                TextField(
                  controller: _locationController,
                  decoration: const InputDecoration(labelText: '地点（可选）'),
                ),
                const SizedBox(height: 12),
                TextField(
                  controller: _detailsController,
                  minLines: 3,
                  maxLines: 5,
                  decoration: const InputDecoration(labelText: '详细说明'),
                ),
                const SizedBox(height: 16),
                FilledButton.tonal(
                  onPressed: _busy ? null : _checkConflicts,
                  child: Text(_busy ? '检测中…' : '执行冲突检测'),
                ),
              ],
            ),
          ),
          if (conflictResult != null)
            _InfoCard(
              title: '冲突检测结果',
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: <Widget>[
                  Text('风险等级：${conflictResult.riskLevel == 'high' ? '高' : '低'}'),
                  const SizedBox(height: 12),
                  if (conflictResult.conflictItems.isEmpty)
                    const Text('未发现冲突，可以确认保存。')
                  else
                    ...conflictResult.conflictItems.map(
                      (item) => ListTile(
                        contentPadding: EdgeInsets.zero,
                        title: Text(item.title),
                        subtitle: Text('${formatDateTime(item.startsAt)} - ${formatDateTime(item.endsAt)}'),
                      ),
                    ),
                  if (conflictResult.suggestions.isNotEmpty) ...<Widget>[
                    const Divider(),
                    const Text('建议时段'),
                    const SizedBox(height: 8),
                    ...conflictResult.suggestions.map(
                      (item) => Text('${item.label}：${formatDateTime(item.candidateStart)}'),
                    ),
                  ],
                  const SizedBox(height: 16),
                  FilledButton(
                    onPressed: _busy ? null : _confirmSchedule,
                    child: const Text('确认保存'),
                  ),
                ],
              ),
            ),
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
