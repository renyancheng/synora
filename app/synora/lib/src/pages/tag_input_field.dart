import 'package:flutter/material.dart';

import '../strings.dart';

class TagInputField extends StatefulWidget {
  const TagInputField({
    super.key,
    required this.initialTags,
    required this.onChanged,
  });

  final List<String> initialTags;
  final ValueChanged<List<String>> onChanged;

  @override
  State<TagInputField> createState() => _TagInputFieldState();
}

class _TagInputFieldState extends State<TagInputField> {
  late final TextEditingController _controller;
  late List<String> _tags;

  @override
  void initState() {
    super.initState();
    _controller = TextEditingController();
    _tags = _normalizeTags(widget.initialTags);
  }

  @override
  void didUpdateWidget(covariant TagInputField oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.initialTags.join('\u0000') != widget.initialTags.join('\u0000')) {
      _tags = _normalizeTags(widget.initialTags);
    }
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  List<String> _normalizeTags(List<String> values) {
    final normalized = <String>[];
    for (final raw in values) {
      final tag = raw.trim();
      if (tag.isNotEmpty && !normalized.contains(tag)) {
        normalized.add(tag);
      }
    }
    return normalized;
  }

  void _emit() {
    widget.onChanged(List<String>.unmodifiable(_tags));
  }

  void _addCurrentInput() {
    final value = _controller.text.trim();
    if (value.isEmpty || _tags.contains(value)) {
      _controller.clear();
      return;
    }
    setState(() {
      _tags = <String>[..._tags, value];
      _controller.clear();
    });
    _emit();
  }

  void _removeTag(String tag) {
    setState(() {
      _tags = _tags.where((item) => item != tag).toList();
    });
    _emit();
  }

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: <Widget>[
        Wrap(
          spacing: 8,
          runSpacing: 8,
          children: _tags
              .map(
                (tag) => InputChip(
                  label: Text(tag),
                  onDeleted: () => _removeTag(tag),
                ),
              )
              .toList(),
        ),
        const SizedBox(height: 12),
        TextField(
          controller: _controller,
          decoration: const InputDecoration(
            labelText: AppStrings.tagsField,
            hintText: AppStrings.addTagHint,
          ),
          onSubmitted: (_) => _addCurrentInput(),
        ),
      ],
    );
  }
}
