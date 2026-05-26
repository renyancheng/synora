import 'package:flutter/material.dart';

import '../date_utils.dart';

class EventDateTimeField extends StatelessWidget {
  const EventDateTimeField({
    super.key,
    required this.label,
    required this.value,
    required this.isAllDay,
    this.enabled = true,
    this.onChanged,
  });

  final String label;
  final DateTime? value;
  final bool isAllDay;
  final bool enabled;
  final ValueChanged<DateTime?>? onChanged;

  Future<void> _pick(BuildContext context) async {
    if (!enabled || onChanged == null) {
      return;
    }
    final now = DateTime.now();
    final initial = value?.toLocal() ?? now;
    final pickedDate = await showDatePicker(
      context: context,
      initialDate: initial,
      firstDate: DateTime(now.year - 5),
      lastDate: DateTime(now.year + 10),
    );
    if (pickedDate == null || !context.mounted) {
      return;
    }
    if (isAllDay) {
      onChanged!(DateTime(pickedDate.year, pickedDate.month, pickedDate.day));
      return;
    }
    final initialTime = TimeOfDay.fromDateTime(initial);
    final pickedTime = await showTimePicker(
      context: context,
      initialTime: initialTime,
    );
    if (pickedTime == null) {
      return;
    }
    onChanged!(
      DateTime(
        pickedDate.year,
        pickedDate.month,
        pickedDate.day,
        pickedTime.hour,
        pickedTime.minute,
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final text = value == null ? '' : (isAllDay ? formatDate(value!) : formatDateTime(value));
    return InkWell(
      onTap: enabled ? () => _pick(context) : null,
      borderRadius: BorderRadius.circular(12),
      child: IgnorePointer(
        child: TextField(
          controller: TextEditingController(text: text),
          enabled: enabled,
          readOnly: true,
          decoration: InputDecoration(
            labelText: label,
            suffixIcon: const Icon(Icons.schedule_outlined),
          ),
        ),
      ),
    );
  }
}
