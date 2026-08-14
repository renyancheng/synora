import 'package:flutter/material.dart';

/// AI 消息上方的规划/感知行：生成中流光（flowing），完成后静态常驻。
class ReasoningBanner extends StatelessWidget {
  const ReasoningBanner({
    super.key,
    required this.text,
    required this.flowing,
  });

  final String text;
  final bool flowing;

  @override
  Widget build(BuildContext context) {
    return Align(
      alignment: Alignment.centerLeft,
      child: Container(
        constraints: const BoxConstraints(maxWidth: 580),
        margin: const EdgeInsets.only(bottom: 6),
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 7),
        decoration: BoxDecoration(
          color: const Color(0xFFF0F7F5),
          borderRadius: BorderRadius.circular(12),
          border: Border.all(color: const Color(0xFFD7E8E2)),
        ),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: <Widget>[
            const Icon(
              Icons.track_changes,
              size: 14,
              color: Color(0xFF176B5A),
            ),
            const SizedBox(width: 8),
            Flexible(
              child: flowing
                  ? _FlowText(
                      text: text,
                      style: const TextStyle(
                        fontSize: 12,
                        color: Color(0xFF275C52),
                      ),
                    )
                  : Text(
                      text,
                      style: const TextStyle(
                        fontSize: 12,
                        color: Color(0xFF275C52),
                      ),
                    ),
            ),
          ],
        ),
      ),
    );
  }
}

/// v0.dev 风格一行流光文字：高亮从文字上扫过，用于"正在思考"的当前行。
class _FlowText extends StatefulWidget {
  const _FlowText({
    required this.text,
    this.style,
  });

  final String text;
  final TextStyle? style;

  @override
  State<_FlowText> createState() => _FlowTextState();
}

class _FlowTextState extends State<_FlowText>
    with SingleTickerProviderStateMixin {
  late final AnimationController _controller;
  late final Animation<double> _progress;

  @override
  void initState() {
    super.initState();
    _controller = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 1500),
    )..repeat();
    _progress = CurvedAnimation(
      parent: _controller,
      curve: const Cubic(0.35, 0, 0.65, 1),
    );
  }

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    _syncAnimation();
  }

  /// 系统“减少动态效果”开启时关闭无限流光动画。
  void _syncAnimation() {
    final disabled = MediaQuery.disableAnimationsOf(context);
    if (disabled) {
      _controller.stop();
    } else if (!_controller.isAnimating) {
      _controller.repeat();
    }
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final disabled = MediaQuery.disableAnimationsOf(context);
    if (disabled) {
      return Text(widget.text, style: widget.style);
    }
    const baseColor = Color(0xFF275C52);
    const highlight = Color(0xFF3EBD93);
    return AnimatedBuilder(
      animation: _progress,
      builder: (context, child) {
        return ShaderMask(
          blendMode: BlendMode.srcATop,
          shaderCallback: (bounds) {
            final offset = -0.3 + _progress.value * 1.6;
            return LinearGradient(
              begin: Alignment.centerLeft,
              end: Alignment.centerRight,
              colors: const <Color>[baseColor, highlight, baseColor],
              stops: const <double>[0.0, 0.45, 0.55],
              transform: _SlidingGradientTransform(offset),
            ).createShader(bounds);
          },
          child: child,
        );
      },
      child: Text(widget.text, style: widget.style),
    );
  }
}

/// 把线性渐变整体平移 [percent] 个自身宽度的变换（Flutter 官方 shimmer 模式）。
class _SlidingGradientTransform extends GradientTransform {
  const _SlidingGradientTransform(this.percent);

  final double percent;

  @override
  Matrix4? transform(Rect bounds, {TextDirection? textDirection}) {
    return Matrix4.translationValues(bounds.width * percent, 0, 0);
  }
}

/// 生成中的呼吸绿点：透明度随时间正弦呼吸；系统“减少动态效果”时显示静态点。
class BreathingDot extends StatefulWidget {
  const BreathingDot({
    super.key,
    this.size = 10,
  });

  final double size;

  @override
  State<BreathingDot> createState() => _BreathingDotState();
}

class _BreathingDotState extends State<BreathingDot>
    with SingleTickerProviderStateMixin {
  late final AnimationController _controller;

  @override
  void initState() {
    super.initState();
    _controller = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 1100),
    )..repeat(reverse: true);
  }

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    _syncAnimation();
  }

  /// 系统“减少动态效果”开启时关闭呼吸动画，显示静态占位点。
  void _syncAnimation() {
    final disabled = MediaQuery.disableAnimationsOf(context);
    if (disabled) {
      _controller.stop();
    } else if (!_controller.isAnimating) {
      _controller.repeat(reverse: true);
    }
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final disabled = MediaQuery.disableAnimationsOf(context);
    if (disabled) {
      return Container(
        width: widget.size,
        height: widget.size,
        decoration: const BoxDecoration(
          color: Color(0xFF176B5A),
          shape: BoxShape.circle,
        ),
      );
    }
    return AnimatedBuilder(
      animation: _controller,
      builder: (context, _) {
        final opacity = 0.35 + 0.65 * _controller.value;
        return Opacity(
          opacity: opacity,
          child: Container(
            width: widget.size,
            height: widget.size,
            decoration: BoxDecoration(
              color: const Color(0xFF176B5A),
              shape: BoxShape.circle,
            ),
          ),
        );
      },
    );
  }
}
