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

String formatDateTime(DateTime? value) {
  if (value == null) {
    return '待补充';
  }
  final local = value.toLocal();
  return '${local.year}年${local.month.toString().padLeft(2, '0')}月${local.day.toString().padLeft(2, '0')}日 '
      '${local.hour.toString().padLeft(2, '0')}:${local.minute.toString().padLeft(2, '0')}';
}

String formatEventRange({
  required EventDateTimeValue start,
  required EventDateTimeValue end,
  required bool isAllDay,
}) {
  final localStart = start.dateTime.toLocal();
  final localEnd = end.dateTime.toLocal();
  if (isAllDay) {
    return '${localStart.year}年${localStart.month.toString().padLeft(2, '0')}月${localStart.day.toString().padLeft(2, '0')}日 全天';
  }
  final sameDay = localStart.year == localEnd.year && localStart.month == localEnd.month && localStart.day == localEnd.day;
  final startLabel = formatDateTime(localStart);
  if (sameDay) {
    return '$startLabel - ${localEnd.hour.toString().padLeft(2, '0')}:${localEnd.minute.toString().padLeft(2, '0')}';
  }
  return '$startLabel - ${formatDateTime(localEnd)}';
}

String formatReminderOffsets(List<int> offsets) {
  if (offsets.isEmpty) {
    return '默认提醒';
  }
  return offsets.map((item) {
    final minutes = item.abs();
      if (minutes >= 1440 && minutes % 1440 == 0) {
        final days = minutes ~/ 1440;
      return '提前 $days 天';
      }
      if (minutes >= 60 && minutes % 60 == 0) {
        final hours = minutes ~/ 60;
      return '提前 $hours 小时';
      }
    return '提前 $minutes 分钟';
  }).join(' / ');
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
