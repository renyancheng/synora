import 'package:flutter/material.dart';

class TagPaletteColors {
  const TagPaletteColors({
    required this.background,
    required this.foreground,
    required this.border,
  });

  final Color background;
  final Color foreground;
  final Color border;
}

class TagPalette {
  static const List<TagPaletteColors> _palette = <TagPaletteColors>[
    TagPaletteColors(background: Color(0xFFE8F3FF), foreground: Color(0xFF0F4C81), border: Color(0xFF9CC3F2)),
    TagPaletteColors(background: Color(0xFFEAF8EE), foreground: Color(0xFF1E6B3A), border: Color(0xFF9ED4AE)),
    TagPaletteColors(background: Color(0xFFFFF1E6), foreground: Color(0xFF9A4D00), border: Color(0xFFF3C69A)),
    TagPaletteColors(background: Color(0xFFF5ECFF), foreground: Color(0xFF5C2C91), border: Color(0xFFC9ACEB)),
    TagPaletteColors(background: Color(0xFFFFEBF0), foreground: Color(0xFF983255), border: Color(0xFFF0B1C5)),
    TagPaletteColors(background: Color(0xFFEAF7F7), foreground: Color(0xFF16666B), border: Color(0xFF9FD0D3)),
  ];

  static TagPaletteColors resolve(String text) {
    final normalized = text.trim();
    if (normalized.isEmpty) {
      return _palette.first;
    }
    final index = normalized.runes.fold<int>(0, (value, rune) => value * 31 + rune) % _palette.length;
    return _palette[index];
  }
}
