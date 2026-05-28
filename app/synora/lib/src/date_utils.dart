import 'models.dart';

DateTime? parseEditableDateTime(String input) {
  final trimmed = input.trim();
  if (trimmed.isEmpty) {
    return null;
  }
  final normalized = trimmed
      .replaceAll('年', '-')
      .replaceAll('月', '-')
      .replaceAll('日', '')
      .replaceAll('/', '-')
      .replaceFirst(' ', 'T');
  return DateTime.tryParse(normalized);
}

String twoDigits(int value) => value.toString().padLeft(2, '0');

String formatDate(DateTime value) {
  final local = value.toLocal();
  return '${local.year}年${twoDigits(local.month)}月${twoDigits(local.day)}日';
}

String formatTime(DateTime value) {
  final local = value.toLocal();
  return '${twoDigits(local.hour)}:${twoDigits(local.minute)}';
}

String formatMonthLabel(DateTime value) {
  final local = value.toLocal();
  return '${local.year}年${local.month}月';
}

String formatDateTime(DateTime? value) {
  if (value == null) {
    return '待补充';
  }
  final local = value.toLocal();
  return '${formatDate(local)} ${formatTime(local)}';
}

String formatEventRange({
  required EventDateTimeValue start,
  required EventDateTimeValue end,
  required bool isAllDay,
}) {
  final localStart = start.dateTime.toLocal();
  final localEnd = end.dateTime.toLocal();
  if (isAllDay) {
    return '${formatDate(localStart)} 全天';
  }
  final sameDay = localStart.year == localEnd.year && localStart.month == localEnd.month && localStart.day == localEnd.day;
  if (sameDay) {
    return '${formatDate(localStart)} ${formatTime(localStart)} - ${formatTime(localEnd)}';
  }
  return '${formatDateTime(localStart)} - ${formatDateTime(localEnd)}';
}

String formatReminderOffsets(List<int> offsets) {
  if (offsets.isEmpty) {
    return '默认提醒';
  }
  return offsets.map((item) {
    final minutes = item.abs();
    if (minutes >= 1440 && minutes % 1440 == 0) {
      return '提前 ${minutes ~/ 1440} 天';
    }
    if (minutes >= 60 && minutes % 60 == 0) {
      return '提前 ${minutes ~/ 60} 小时';
    }
    return '提前 $minutes 分钟';
  }).join(' / ');
}

const Map<String, String> reminderPresetLabels = <String, String>{
  'immediate': '立刻提醒',
  '30m_before': '提前 30 分钟',
  '1h_before': '提前 1 小时',
  '2h_before': '提前 2 小时',
  'same_day_0900': '当天 09:00',
  'previous_day_1700': '前一天 17:00',
  'previous_day_0900': '前一天 09:00',
  'two_days_before_0900': '前 2 天 09:00',
};

const List<String> reminderPresetOptions = <String>[
  'immediate',
  '30m_before',
  '1h_before',
  '2h_before',
  'same_day_0900',
  'previous_day_1700',
  'previous_day_0900',
  'two_days_before_0900',
];

String formatReminderPreset(String preset) {
  return reminderPresetLabels[preset] ?? reminderPresetLabels['previous_day_1700']!;
}

String formatRecurrence(List<String> rules) {
  if (rules.isEmpty) {
    return '不重复';
  }
  return rules.map((rule) {
    if (rule.contains('FREQ=DAILY')) {
      return '每天';
    }
    if (rule.contains('FREQ=WEEKLY')) {
      return '每周';
    }
    if (rule.contains('FREQ=MONTHLY')) {
      return '每月';
    }
    return rule;
  }).join('，');
}

bool isSameDay(DateTime a, DateTime b) {
  final localA = a.toLocal();
  final localB = b.toLocal();
  return localA.year == localB.year && localA.month == localB.month && localA.day == localB.day;
}
