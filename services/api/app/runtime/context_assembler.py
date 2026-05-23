from __future__ import annotations


class ContextAssembler:
    def build_schedule_context(
        self,
        *,
        text_content: str,
        attachment_texts: list[str],
    ) -> dict[str, str]:
        merged_parts = [text_content.strip()] if text_content.strip() else []
        merged_parts.extend(item.strip() for item in attachment_texts if item.strip())
        return {"merged_text": "\n\n".join(merged_parts).strip()}

    def build_quick_note_context(
        self,
        *,
        text_content: str,
        attachment_texts: list[str],
        manual_tags: list[str],
    ) -> dict[str, str | list[str]]:
        merged_parts = [text_content.strip()] if text_content.strip() else []
        merged_parts.extend(item.strip() for item in attachment_texts if item.strip())
        return {
            "merged_text": "\n\n".join(merged_parts).strip(),
            "manual_tags": manual_tags,
        }
