from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from openai import OpenAI

from app.config import Settings, get_settings


class ModelAdapter:
    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._client = OpenAI(
            api_key=self._settings.deepseek_api_key,
            base_url=self._settings.deepseek_base_url,
            timeout=self._settings.deepseek_timeout_seconds,
        )

    def _json_completion(self, system_prompt: str, user_prompt: str) -> dict:
        if not self._settings.deepseek_api_key:
            raise ValueError("未配置 DeepSeek API Key。")
        response = self._client.chat.completions.create(
            model=self._settings.deepseek_model,
            temperature=0.1,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        content = response.choices[0].message.content or "{}"
        return json.loads(content)

    @staticmethod
    def _looks_like_precise_schedule(text: str) -> bool:
        precise_patterns = [
            r"\d{1,2}[:：]\d{1,2}",
            r"\d{1,2}点半?",
            r"(今天|明天|后天).{0,8}(\d{1,2}[:：]\d{1,2}|\d{1,2}点)",
            r"(本周|这周|下周)[一二三四五六日天].{0,8}(\d{1,2}[:：]\d{1,2}|\d{1,2}点)",
            r"\d{1,2}月\d{1,2}日.{0,8}(\d{1,2}[:：]\d{1,2}|\d{1,2}点)",
        ]
        return any(re.search(pattern, text) for pattern in precise_patterns)

    @staticmethod
    def _fallback_route_workflow(payload: dict) -> str:
        source_type = str(payload.get("source_type", "text"))
        if source_type in {"screenshot", "photo"}:
            return "schedule_intake"
        text = str(payload.get("text_content") or payload.get("content") or "")
        if ModelAdapter._looks_like_precise_schedule(text):
            return "schedule_intake"
        schedule_context_hints = [
            r"(会议|开会|上课|答辩|教研会|日程)",
            r"(教室|会议室|信息楼|实验室|办公室|A\d{3}|B\d{3})",
        ]
        if all(re.search(pattern, text) for pattern in schedule_context_hints):
            return "schedule_intake"
        return "quick_note_intake"

    def route_workflow(self, payload: dict) -> str:
        if payload.get("preferred_workflow") in {"schedule_intake", "quick_note_intake"}:
            return str(payload["preferred_workflow"])

        if not self._settings.deepseek_api_key:
            return self._fallback_route_workflow(payload)

        try:
            result = self._json_completion(
                "你是意图路由器，只能输出 JSON，并且 workflow 只能是 schedule_intake 或 quick_note_intake。",
                json.dumps(
                    {
                        "source_type": payload.get("source_type"),
                        "text_content": payload.get("text_content"),
                        "attachment_ids": payload.get("attachment_ids", []),
                        "context": payload.get("context", {}),
                    },
                    ensure_ascii=False,
                ),
            )
            workflow = str(result.get("workflow") or "").strip()
            if workflow in {"schedule_intake", "quick_note_intake"}:
                heuristic_workflow = self._fallback_route_workflow(payload)
                if workflow == "schedule_intake" and heuristic_workflow == "quick_note_intake":
                    return heuristic_workflow
                return workflow
        except Exception:
            pass

        return self._fallback_route_workflow(payload)

    def extract_schedule(
        self,
        *,
        merged_text: str,
        source_type: str,
        timezone_name: str,
        reference_time: datetime,
    ) -> dict:
        system_prompt = """
你是严格的日程抽取助手。请从输入文本中抽取日程草稿，并且只输出 JSON。
必须返回字段：title, location, details, scheduled_at, duration_minutes, missing_fields, ambiguity_flags, parse_confidence, evidence_digest
规则：
1. 如果文本中有“明天、后天、下周三、今天下午3点”等相对时间，请结合 reference_time 和 timezone_name 解析为精确的 ISO 8601 时间，必须带时区偏移。
2. scheduled_at 无法确定时返回 null。
3. duration_minutes 默认 60。
4. missing_fields 只允许包含 title、scheduled_at。
5. ambiguity_flags 可包含 time_ambiguous、year_inferred、location_ambiguous。
6. evidence_digest 返回 1-5 条中文摘要，用于前端展示提取依据。
7. details 使用简洁中文概括事项。
8. title 不超过 30 个汉字。
"""
        user_prompt = json.dumps(
            {
                "source_type": source_type,
                "timezone_name": timezone_name,
                "reference_time": reference_time.isoformat(),
                "content": merged_text,
            },
            ensure_ascii=False,
        )
        return self._json_completion(system_prompt, user_prompt)

    def suggest_quick_note_tags(self, *, merged_text: str, manual_tags: list[str], source_type: str) -> dict:
        system_prompt = """
你是速记整理助手。请对输入内容做简要规范化，并且只输出 JSON。
必须返回字段：normalized_content, preview_tags, evidence_digest
规则：
1. normalized_content 保留原意，去掉明显噪音。
2. preview_tags 返回 2-5 个中文标签，优先使用教学、科研、会议、生活、待办等主题。
3. evidence_digest 返回 1-4 条中文说明，解释标签依据。
4. 要融合 manual_tags 中的人工标签，避免遗漏。
"""
        user_prompt = json.dumps(
            {
                "source_type": source_type,
                "content": merged_text,
                "manual_tags": manual_tags,
            },
            ensure_ascii=False,
        )
        return self._json_completion(system_prompt, user_prompt)

    @staticmethod
    def compute_reminder_at(scheduled_at: datetime) -> datetime:
        reminder_at = scheduled_at - timedelta(days=1)
        now = datetime.now(ZoneInfo("UTC"))
        if reminder_at <= now:
            reminder_at = scheduled_at - timedelta(minutes=30)
        if reminder_at <= now:
            reminder_at = now + timedelta(minutes=5)
        return reminder_at
