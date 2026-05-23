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
  final year = local.year.toString().padLeft(4, '0');
  final month = local.month.toString().padLeft(2, '0');
  final day = local.day.toString().padLeft(2, '0');
  final hour = local.hour.toString().padLeft(2, '0');
  final minute = local.minute.toString().padLeft(2, '0');
  return '$year年$month月$day日 $hour:$minute';
}
