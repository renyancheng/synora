import 'package:flutter/material.dart';

import '../app_controller.dart';
import '../date_utils.dart';
import '../models.dart';
import '../strings.dart';

class ScheduleListPage extends StatefulWidget {
  const ScheduleListPage({super.key, required this.controller});

  final AppController controller;

  @override
  State<ScheduleListPage> createState() => _ScheduleListPageState();
}

class _ScheduleListPageState extends State<ScheduleListPage> {
  late DateTime _visibleMonth;
  late DateTime _selectedDay;

  @override
  void initState() {
    super.initState();
    final now = DateTime.now();
    _visibleMonth = DateTime(now.year, now.month);
    _selectedDay = DateTime(now.year, now.month, now.day);
  }

  Future<void> _showDetails(BuildContext context, ScheduleItem item) async {
    await showDialog<void>(
      context: context,
      builder: (context) => AlertDialog(
        title: Text(item.title),
        content: SingleChildScrollView(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: <Widget>[
              Text('${AppStrings.startField}：${formatEventRange(start: item.start, end: item.end, isAllDay: item.isAllDay)}'),
              const SizedBox(height: 8),
              if (item.location != null && item.location!.trim().isNotEmpty) ...<Widget>[
                Text('${AppStrings.locationField}：${item.location}'),
                const SizedBox(height: 8),
              ],
              Text('${AppStrings.reminderField}：${formatReminderOffsets(item.reminderOffsetsMinutes)}'),
              const SizedBox(height: 8),
              Text('${AppStrings.recurrenceField}：${formatRecurrence(item.recurrence)}'),
              const SizedBox(height: 8),
              Text('${AppStrings.timeZoneField}：${item.start.timeZone}'),
              const SizedBox(height: 8),
              Text('${AppStrings.parseConfidenceField}：${(item.parseConfidence * 100).toStringAsFixed(0)}%'),
              const SizedBox(height: 8),
              Text('${AppStrings.sourceTextField}：${item.sourceText.trim().isEmpty ? AppStrings.noContent : item.sourceText}'),
              const SizedBox(height: 8),
              Text('${AppStrings.detailsField}：${item.details.trim().isEmpty ? AppStrings.noContent : item.details}'),
            ],
          ),
        ),
        actions: <Widget>[
          TextButton(
            onPressed: () => Navigator.of(context).pop(),
            child: const Text(AppStrings.confirmAction),
          ),
        ],
      ),
    );
  }

  Future<void> _showDeleteMenu(BuildContext context, ScheduleItem item) async {
    await showModalBottomSheet<void>(
      context: context,
      builder: (sheetContext) => SafeArea(
        child: ListTile(
          leading: const Icon(Icons.delete_outline),
          title: const Text(AppStrings.delete),
          onTap: () async {
            Navigator.of(sheetContext).pop();
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
            if (confirmed == true) {
              await widget.controller.deleteScheduleItem(item.id);
              if (context.mounted) {
                ScaffoldMessenger.of(context).showSnackBar(
                  const SnackBar(content: Text(AppStrings.deleteDone)),
                );
              }
            }
          },
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    return AnimatedBuilder(
      animation: widget.controller,
      builder: (context, _) {
        final schedules = List<ScheduleItem>.from(widget.controller.schedules)
          ..sort((a, b) => a.start.dateTime.compareTo(b.start.dateTime));
        final filteredSchedules = schedules.where((item) => isSameDay(item.start.dateTime, _selectedDay)).toList();

        return Scaffold(
          appBar: AppBar(title: const Text(AppStrings.scheduleListTitle)),
          body: ListView(
            padding: const EdgeInsets.all(16),
            children: <Widget>[
              Card(
                child: Padding(
                  padding: const EdgeInsets.all(16),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: <Widget>[
                      Row(
                        children: <Widget>[
                          Text(AppStrings.monthViewTitle, style: Theme.of(context).textTheme.titleMedium),
                          const Spacer(),
                          IconButton(
                            onPressed: () {
                              setState(() {
                                _visibleMonth = DateTime(_visibleMonth.year, _visibleMonth.month - 1);
                              });
                            },
                            icon: const Icon(Icons.chevron_left),
                            tooltip: AppStrings.previousMonth,
                          ),
                          Text(formatMonthLabel(_visibleMonth), style: Theme.of(context).textTheme.titleSmall),
                          IconButton(
                            onPressed: () {
                              setState(() {
                                _visibleMonth = DateTime(_visibleMonth.year, _visibleMonth.month + 1);
                              });
                            },
                            icon: const Icon(Icons.chevron_right),
                            tooltip: AppStrings.nextMonth,
                          ),
                        ],
                      ),
                      const SizedBox(height: 12),
                      _MonthCalendar(
                        visibleMonth: _visibleMonth,
                        selectedDay: _selectedDay,
                        schedules: schedules,
                        onDaySelected: (day) {
                          setState(() {
                            _selectedDay = day;
                          });
                        },
                      ),
                    ],
                  ),
                ),
              ),
              const SizedBox(height: 16),
              Text(
                '${AppStrings.dayScheduleTitle} · ${formatDate(_selectedDay)}',
                style: Theme.of(context).textTheme.titleMedium,
              ),
              const SizedBox(height: 12),
              if (filteredSchedules.isEmpty)
                const _EmptyPanel(AppStrings.emptyDaySchedules)
              else
                ...filteredSchedules.map(
                  (item) => Card(
                    margin: const EdgeInsets.only(bottom: 12),
                    child: ListTile(
                      title: Text(item.title),
                      subtitle: Text(
                        '${formatEventRange(start: item.start, end: item.end, isAllDay: item.isAllDay)}'
                        '${item.location == null || item.location!.trim().isEmpty ? '' : '\n${item.location}'}\n'
                        '${formatRecurrence(item.recurrence)}',
                      ),
                      isThreeLine: true,
                      onTap: () => _showDetails(context, item),
                      onLongPress: () => _showDeleteMenu(context, item),
                    ),
                  ),
                ),
            ],
          ),
        );
      },
    );
  }
}

class _MonthCalendar extends StatelessWidget {
  const _MonthCalendar({
    required this.visibleMonth,
    required this.selectedDay,
    required this.schedules,
    required this.onDaySelected,
  });

  final DateTime visibleMonth;
  final DateTime selectedDay;
  final List<ScheduleItem> schedules;
  final ValueChanged<DateTime> onDaySelected;

  @override
  Widget build(BuildContext context) {
    final firstDay = DateTime(visibleMonth.year, visibleMonth.month, 1);
    final daysInMonth = DateTime(visibleMonth.year, visibleMonth.month + 1, 0).day;
    final startOffset = firstDay.weekday - 1;
    final totalCells = ((startOffset + daysInMonth) / 7).ceil() * 7;
    final scheduleDays = schedules
        .where((item) => item.start.dateTime.year == visibleMonth.year && item.start.dateTime.month == visibleMonth.month)
        .map((item) {
          final date = item.start.dateTime.toLocal();
          return DateTime(date.year, date.month, date.day);
        })
        .toSet();

    const weekLabels = <String>['一', '二', '三', '四', '五', '六', '日'];

    return Column(
      children: <Widget>[
        Row(
          children: weekLabels
              .map(
                (label) => Expanded(
                  child: Center(
                    child: Padding(
                      padding: const EdgeInsets.symmetric(vertical: 6),
                      child: Text(label, style: Theme.of(context).textTheme.bodySmall),
                    ),
                  ),
                ),
              )
              .toList(),
        ),
        GridView.builder(
          shrinkWrap: true,
          physics: const NeverScrollableScrollPhysics(),
          gridDelegate: const SliverGridDelegateWithFixedCrossAxisCount(
            crossAxisCount: 7,
            childAspectRatio: 1,
          ),
          itemCount: totalCells,
          itemBuilder: (context, index) {
            final dayNumber = index - startOffset + 1;
            if (dayNumber <= 0 || dayNumber > daysInMonth) {
              return const SizedBox.shrink();
            }
            final day = DateTime(visibleMonth.year, visibleMonth.month, dayNumber);
            final isSelected = isSameDay(day, selectedDay);
            final isToday = isSameDay(day, DateTime.now());
            final hasSchedule = scheduleDays.contains(day);

            return InkWell(
              borderRadius: BorderRadius.circular(14),
              onTap: () => onDaySelected(day),
              child: Container(
                margin: const EdgeInsets.all(4),
                decoration: BoxDecoration(
                  color: isSelected ? const Color(0xFF176B5A) : (isToday ? const Color(0xFFEAF5F1) : Colors.transparent),
                  borderRadius: BorderRadius.circular(14),
                ),
                child: Column(
                  mainAxisAlignment: MainAxisAlignment.center,
                  children: <Widget>[
                    Text(
                      '$dayNumber',
                      style: TextStyle(
                        color: isSelected ? Colors.white : const Color(0xFF173C35),
                        fontWeight: isToday ? FontWeight.w700 : FontWeight.w500,
                      ),
                    ),
                    const SizedBox(height: 4),
                    AnimatedOpacity(
                      opacity: hasSchedule ? 1 : 0,
                      duration: const Duration(milliseconds: 160),
                      child: Container(
                        width: 6,
                        height: 6,
                        decoration: BoxDecoration(
                          color: isSelected ? Colors.white : const Color(0xFF176B5A),
                          shape: BoxShape.circle,
                        ),
                      ),
                    ),
                  ],
                ),
              ),
            );
          },
        ),
      ],
    );
  }
}

class _EmptyPanel extends StatelessWidget {
  const _EmptyPanel(this.text);

  final String text;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(20),
      decoration: BoxDecoration(
        color: const Color(0xFFF5FAF8),
        borderRadius: BorderRadius.circular(20),
      ),
      child: Text(text),
    );
  }
}
