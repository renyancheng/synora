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
        source_history: list[str] | None = None,
        previous_draft_summary: str = "",
    ) -> dict[str, str]:
        normalized_history = [item.strip() for item in (source_history or []) if item and item.strip()]
        latest_user_text = text_content.strip()
        if latest_user_text and (not normalized_history or normalized_history[-1] != latest_user_text):
            normalized_history.append(latest_user_text)

        source_text = "\n\n".join(normalized_history).strip()
        attachment_section = "\n\n".join(item.strip() for item in attachment_texts if item.strip()).strip()
        memory_context = self.build_memory_context(memory_summary=memory_summary, memory_items=memory_items)

        prompt_sections: list[str] = []
        if source_text:
            prompt_sections.append(f"用户原话历史：\n{source_text}")
        if latest_user_text:
            prompt_sections.append(f"本轮最新更正：\n{latest_user_text}")
        if previous_draft_summary.strip():
            prompt_sections.append(f"上一版规范化草稿：\n{previous_draft_summary.strip()}")
        if attachment_section:
            prompt_sections.append(f"附件证据：\n{attachment_section}")
        if memory_context:
            prompt_sections.append(f"记忆提示（仅供参考）：\n{memory_context}")

        return {
            "merged_text": source_text,
            "source_text": source_text,
            "latest_user_text": latest_user_text,
            "attachment_text": attachment_section,
            "memory_context": memory_context,
            "prompt_text": "\n\n".join(prompt_sections).strip(),
        }

    def build_quick_note_context(
        self,
        *,
        text_content: str,
        attachment_texts: list[str],
        manual_tags: list[str],
        previous_note_content: str = "",
        latest_user_text: str = "",
    ) -> dict[str, str | list[str]]:
        current_input = text_content.strip()
        previous_note = previous_note_content.strip()
        latest_correction = latest_user_text.strip() or current_input

        merged_parts: list[str] = []
        if previous_note:
            merged_parts.append(previous_note)
        if latest_correction and latest_correction not in merged_parts:
            merged_parts.append(latest_correction)
        merged_parts.extend(item.strip() for item in attachment_texts if item.strip())
        base_text = "\n\n".join(merged_parts).strip()

        prompt_sections: list[str] = []
        if current_input and not previous_note:
            prompt_sections.append(f"当前输入：\n{current_input}")
        if previous_note:
            prompt_sections.append(f"上一版待确认速记：\n{previous_note}")
        if latest_correction and previous_note:
            prompt_sections.append(f"本轮补充或修正：\n{latest_correction}")
        attachment_section = "\n\n".join(item.strip() for item in attachment_texts if item.strip()).strip()
        if attachment_section:
            prompt_sections.append(f"附件证据：\n{attachment_section}")
        if manual_tags:
            prompt_sections.append("手动标签：\n" + "、".join(manual_tags))

        return {
            "merged_text": base_text,
            "prompt_text": "\n\n".join(prompt_sections).strip(),
            "manual_tags": manual_tags,
        }
