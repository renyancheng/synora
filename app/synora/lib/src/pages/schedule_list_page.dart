import 'dart:async';

import 'package:flutter/material.dart';

import '../app_controller.dart';
import '../date_utils.dart';
import '../models.dart';
import '../strings.dart';
import 'schedule_detail_page.dart';

class ScheduleListPage extends StatefulWidget {
  const ScheduleListPage({super.key, required this.controller});

  final AppController controller;

  @override
  State<ScheduleListPage> createState() => _ScheduleListPageState();
}

class _ScheduleListPageState extends State<ScheduleListPage> {
  late DateTime _visibleMonth;
  late DateTime _selectedDay;
  late Future<List<ScheduleItem>> _future;
  final TextEditingController _searchController = TextEditingController();
  Timer? _searchDebounce;
  String _searchQuery = '';

  @override
  void initState() {
    super.initState();
    final now = DateTime.now();
    _visibleMonth = DateTime(now.year, now.month);
    _selectedDay = DateTime(now.year, now.month, now.day);
    _future = _loadSchedules();
  }

  Future<void> _openDetails(ScheduleItem item) async {
    final changed = await Navigator.of(context).push<bool>(
      MaterialPageRoute<bool>(
        builder: (_) =>
            ScheduleDetailPage(controller: widget.controller, item: item),
      ),
    );
    if (changed == true && mounted) {
      await _reload();
    }
  }

  Future<List<ScheduleItem>> _loadSchedules({
    bool alignSelectedDay = false,
  }) async {
    final schedules = List<ScheduleItem>.from(
      await widget.controller.fetchSchedules(query: _searchQuery),
    )..sort((a, b) => a.start.dateTime.compareTo(b.start.dateTime));
    if (alignSelectedDay && schedules.isNotEmpty) {
      final first = schedules.first.start.dateTime.toLocal();
      _visibleMonth = DateTime(first.year, first.month);
      _selectedDay = DateTime(first.year, first.month, first.day);
    }
    return schedules;
  }

  Future<void> _reload({bool alignSelectedDay = false}) async {
    setState(() {
      _future = _loadSchedules(alignSelectedDay: alignSelectedDay);
    });
  }

  void _handleSearchChanged(String value) {
    _searchDebounce?.cancel();
    _searchDebounce = Timer(const Duration(milliseconds: 300), () {
      if (!mounted) {
        return;
      }
      setState(() {
        _searchQuery = value.trim();
        _future = _loadSchedules(alignSelectedDay: _searchQuery.isNotEmpty);
      });
    });
  }

  @override
  void dispose() {
    _searchDebounce?.cancel();
    _searchController.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text(AppStrings.scheduleListTitle)),
      body: FutureBuilder<List<ScheduleItem>>(
        future: _future,
        builder: (context, snapshot) {
          if (snapshot.connectionState != ConnectionState.done) {
            return const Center(child: CircularProgressIndicator());
          }
          if (snapshot.hasError) {
            return Center(child: Text(snapshot.error.toString()));
          }
          final schedules = snapshot.data ?? const <ScheduleItem>[];
          final filteredSchedules = schedules
              .where((item) => isSameDay(item.start.dateTime, _selectedDay))
              .toList();

          return ListView(
            padding: const EdgeInsets.all(16),
            children: <Widget>[
              Padding(
                padding: const EdgeInsets.only(bottom: 12),
                child: TextField(
                  controller: _searchController,
                  onChanged: _handleSearchChanged,
                  decoration: InputDecoration(
                    hintText: AppStrings.searchSchedulesHint,
                    prefixIcon: const Icon(Icons.search),
                    suffixIcon: _searchQuery.isEmpty
                        ? null
                        : IconButton(
                            onPressed: () {
                              _searchController.clear();
                              _handleSearchChanged('');
                            },
                            icon: const Icon(Icons.close),
                          ),
                    border: OutlineInputBorder(
                      borderRadius: BorderRadius.circular(16),
                    ),
                  ),
                ),
              ),
              Card(
                child: Padding(
                  padding: const EdgeInsets.all(16),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: <Widget>[
                      Row(
                        children: <Widget>[
                          Text(
                            AppStrings.monthViewTitle,
                            style: Theme.of(context).textTheme.titleMedium,
                          ),
                          const Spacer(),
                          IconButton(
                            onPressed: () {
                              setState(() {
                                _visibleMonth = DateTime(
                                  _visibleMonth.year,
                                  _visibleMonth.month - 1,
                                );
                              });
                            },
                            icon: const Icon(Icons.chevron_left),
                            tooltip: AppStrings.previousMonth,
                          ),
                          Text(
                            formatMonthLabel(_visibleMonth),
                            style: Theme.of(context).textTheme.titleSmall,
                          ),
                          IconButton(
                            onPressed: () {
                              setState(() {
                                _visibleMonth = DateTime(
                                  _visibleMonth.year,
                                  _visibleMonth.month + 1,
                                );
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
                      onTap: () => _openDetails(item),
                    ),
                  ),
                ),
            ],
          );
        },
      ),
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
    final daysInMonth = DateTime(
      visibleMonth.year,
      visibleMonth.month + 1,
      0,
    ).day;
    final startOffset = firstDay.weekday - 1;
    final totalCells = ((startOffset + daysInMonth) / 7).ceil() * 7;
    final scheduleDays = schedules
        .where(
          (item) =>
              item.start.dateTime.year == visibleMonth.year &&
              item.start.dateTime.month == visibleMonth.month,
        )
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
                      child: Text(
                        label,
                        style: Theme.of(context).textTheme.bodySmall,
                      ),
                    ),
                  ),
                ),
              )
              .toList(),
        ),
        const SizedBox(height: 4),
        GridView.builder(
          shrinkWrap: true,
          physics: const NeverScrollableScrollPhysics(),
          itemCount: totalCells,
          gridDelegate: const SliverGridDelegateWithFixedCrossAxisCount(
            crossAxisCount: 7,
            childAspectRatio: 1.05,
          ),
          itemBuilder: (context, index) {
            final dayNumber = index - startOffset + 1;
            if (dayNumber < 1 || dayNumber > daysInMonth) {
              return const SizedBox.shrink();
            }
            final day = DateTime(
              visibleMonth.year,
              visibleMonth.month,
              dayNumber,
            );
            final normalizedDay = DateTime(day.year, day.month, day.day);
            final selected = isSameDay(day, selectedDay);
            final hasSchedule = scheduleDays.contains(normalizedDay);
            return Padding(
              padding: const EdgeInsets.all(4),
              child: InkWell(
                borderRadius: BorderRadius.circular(14),
                onTap: () => onDaySelected(day),
                child: Ink(
                  decoration: BoxDecoration(
                    color: selected
                        ? Theme.of(context).colorScheme.primaryContainer
                        : null,
                    borderRadius: BorderRadius.circular(14),
                  ),
                  child: Stack(
                    alignment: Alignment.center,
                    children: <Widget>[
                      Text('$dayNumber'),
                      if (hasSchedule)
                        Positioned(
                          bottom: 8,
                          child: Container(
                            width: 6,
                            height: 6,
                            decoration: BoxDecoration(
                              color: Theme.of(context).colorScheme.primary,
                              shape: BoxShape.circle,
                            ),
                          ),
                        ),
                    ],
                  ),
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
