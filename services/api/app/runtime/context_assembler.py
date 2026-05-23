from __future__ import annotations

from app.schemas.common import SourceType


class ContextAssembler:
    def build_schedule_context(
        self,
        *,
        source_type: SourceType,
        text_content: str,
        attachment_summaries: list[str],
    ) -> dict[str, str]:
        merged_parts = [text_content.strip()] if text_content.strip() else []
        merged_parts.extend(summary for summary in attachment_summaries if summary.strip())
        merged_text = "\n\n".join(merged_parts).strip()
        return {
            "source_type": source_type,
            "merged_text": merged_text,
        }

    def build_quick_note_context(
        self,
        *,
        source_type: SourceType,
        text_content: str,
        attachment_summaries: list[str],
        manual_tags: list[str],
    ) -> dict[str, str | list[str]]:
        merged_parts = [text_content.strip()] if text_content.strip() else []
        merged_parts.extend(summary for summary in attachment_summaries if summary.strip())
        merged_text = "\n\n".join(merged_parts).strip()
        return {
            "source_type": source_type,
            "merged_text": merged_text,
            "manual_tags": manual_tags,
        }
