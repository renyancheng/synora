from __future__ import annotations


class ContextAssembler:
    @staticmethod
    def build_memory_context(*, memory_summary: str, memory_items: list[dict] | None = None) -> str:
        parts: list[str] = []
        if memory_summary.strip():
            parts.append(f"长期记忆摘要：\n{memory_summary.strip()}")
        items = memory_items or []
        if items:
            lines = [
                f"- {str(item.get('title') or '长期记忆')}：{str(item.get('content') or '').strip()}"
                for item in items
                if str(item.get("content") or "").strip()
            ]
            if lines:
                parts.append("相关长期记忆：\n" + "\n".join(lines))
        return "\n\n".join(parts).strip()

    def build_schedule_context(
        self,
        *,
        text_content: str,
        attachment_texts: list[str],
        memory_summary: str = "",
        memory_items: list[dict] | None = None,
    ) -> dict[str, str]:
        merged_parts = [text_content.strip()] if text_content.strip() else []
        merged_parts.extend(item.strip() for item in attachment_texts if item.strip())
        base_text = "\n\n".join(merged_parts).strip()
        memory_context = self.build_memory_context(memory_summary=memory_summary, memory_items=memory_items)
        if memory_context:
            return {
                "merged_text": base_text,
                "memory_context": memory_context,
                "prompt_text": f"{memory_context}\n\n当前输入：\n{base_text}".strip(),
            }
        return {"merged_text": base_text, "memory_context": "", "prompt_text": base_text}

    def build_quick_note_context(
        self,
        *,
        text_content: str,
        attachment_texts: list[str],
        manual_tags: list[str],
        memory_summary: str = "",
        memory_items: list[dict] | None = None,
    ) -> dict[str, str | list[str]]:
        merged_parts = [text_content.strip()] if text_content.strip() else []
        merged_parts.extend(item.strip() for item in attachment_texts if item.strip())
        base_text = "\n\n".join(merged_parts).strip()
        memory_context = self.build_memory_context(memory_summary=memory_summary, memory_items=memory_items)
        return {
            "merged_text": base_text,
            "memory_context": memory_context,
            "prompt_text": f"{memory_context}\n\n当前输入：\n{base_text}".strip() if memory_context else base_text,
            "manual_tags": manual_tags,
        }
