DateTime? parseEditableDateTime(String input) {
  final trimmed = input.trim();
  if (trimmed.isEmpty) {
    return null;
  }
  final normalized = trimmed.contains('T') ? trimmed : trimmed.replaceFirst(' ', 'T');
  return DateTime.tryParse(normalized);
}

String formatDateTime(DateTime? value) {
  if (value == null) {
    return 'TBD';
  }
  final local = value.toLocal();
  final year = local.year.toString().padLeft(4, '0');
  final month = local.month.toString().padLeft(2, '0');
  final day = local.day.toString().padLeft(2, '0');
  final hour = local.hour.toString().padLeft(2, '0');
  final minute = local.minute.toString().padLeft(2, '0');
  return '$year-$month-$day $hour:$minute';
}

DateTime computeReminderAt(DateTime scheduledAt) {
  final now = DateTime.now();
  var reminderAt = scheduledAt.subtract(const Duration(days: 1));
  if (!reminderAt.isAfter(now)) {
    reminderAt = scheduledAt.subtract(const Duration(minutes: 30));
  }
  if (!reminderAt.isAfter(now)) {
    reminderAt = now.add(const Duration(minutes: 5));
  }
  return reminderAt;
}
