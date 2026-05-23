from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Iterable
from zoneinfo import ZoneInfo

from openai import OpenAI

from app.config import Settings, get_settings
from app.runtime.output_normalizer import OutputNormalizer


class ModelAdapter:
    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._client = OpenAI(
            api_key=self._settings.llm_api_key,
            base_url=self._settings.llm_base_url,
            timeout=self._settings.llm_timeout_seconds,
        )

    def _require_api_key(self) -> None:
        if not self._settings.llm_api_key:
            raise ValueError("未配置大模型 API Key。")

    def _build_message_content(self, *, user_text: str, attachment_parts: list[dict] | None = None) -> list[dict]:
        content: list[dict] = []
        if user_text.strip():
            content.append({"type": "text", "text": user_text.strip()})
        content.extend(attachment_parts or [])
        return content or [{"type": "text", "text": "请基于当前输入完成分析。"}]

    def _json_completion(
        self,
        *,
        system_prompt: str,
        user_text: str,
        attachment_parts: list[dict] | None = None,
    ) -> dict:
        self._require_api_key()
        response = self._client.chat.completions.create(
            model=self._settings.llm_model,
            temperature=0.1,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": self._build_message_content(user_text=user_text, attachment_parts=attachment_parts)},
            ],
        )
        content = response.choices[0].message.content or "{}"
        return json.loads(content)

    def _text_completion(
        self,
        *,
        system_prompt: str,
        user_text: str,
        attachment_parts: list[dict] | None = None,
    ) -> str:
        self._require_api_key()
        response = self._client.chat.completions.create(
            model=self._settings.llm_model,
            temperature=0.3,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": self._build_message_content(user_text=user_text, attachment_parts=attachment_parts)},
            ],
        )
        return (response.choices[0].message.content or "").strip()

    def stream_text_completion(
        self,
        *,
        system_prompt: str,
        user_text: str,
        attachment_parts: list[dict] | None = None,
    ) -> Iterable[str]:
        self._require_api_key()
        stream = self._client.chat.completions.create(
            model=self._settings.llm_model,
            temperature=0.3,
            stream=True,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": self._build_message_content(user_text=user_text, attachment_parts=attachment_parts)},
            ],
        )
        for chunk in stream:
            delta = chunk.choices[0].delta.content or ""
            if delta:
                yield delta

    @staticmethod
    def _looks_like_precise_schedule(text: str) -> bool:
        precise_patterns = [
            r"\d{1,2}:\d{1,2}",
            r"\d{1,2}点半?",
            r"(今天|明天|后天).{0,8}(\d{1,2}:\d{1,2}|\d{1,2}点)",
            r"(本周|这周|下周)[一二三四五六日天].{0,8}(\d{1,2}:\d{1,2}|\d{1,2}点)",
            r"\d{1,2}月\d{1,2}日.{0,8}(\d{1,2}:\d{1,2}|\d{1,2}点)",
        ]
        return any(re.search(pattern, text) for pattern in precise_patterns)

    @staticmethod
    def _looks_like_quick_note(text: str) -> bool:
        if not text.strip():
            return False
        explicit_keywords = ["记一下", "帮我记", "记住", "速记", "备忘", "记录一下", "存一个", "灵感", "想法", "待办"]
        return any(keyword in text for keyword in explicit_keywords)

    @staticmethod
    def _looks_like_general_chat(text: str) -> bool:
        if not text.strip():
            return True
        general_keywords = ["你好", "hi", "hello", "谢谢", "怎么", "为什么", "介绍", "你是谁", "帮我分析", "聊聊", "建议"]
        lower = text.lower()
        return any(keyword in lower for keyword in general_keywords) or "？" in text or "?" in text

    @staticmethod
    def _fallback_route_workflow(payload: dict) -> str:
        selected_tool = payload.get("selected_tool")
        text = str(payload.get("text_content") or payload.get("content") or "")
        attachment_ids = list(payload.get("attachment_ids") or [])
        if selected_tool == "schedule":
            return "schedule_intake"
        if selected_tool == "quick_note":
            return "quick_note_intake"
        if attachment_ids:
            return "schedule_intake" if ModelAdapter._looks_like_precise_schedule(text) else "quick_note_intake"
        if ModelAdapter._looks_like_precise_schedule(text):
            return "schedule_intake"
        if ModelAdapter._looks_like_quick_note(text):
            return "quick_note_intake"
        schedule_context_hints = [
            r"(会议|开会|上课|答辩|教研会|日程)",
            r"(教室|会议室|信息楼|实验室|办公室|A\d{3}|B\d{3})",
        ]
        if all(re.search(pattern, text) for pattern in schedule_context_hints):
            return "schedule_intake"
        return "quick_note_intake"

    @staticmethod
    def _fallback_conversation_intent(payload: dict) -> str:
        selected_tool = payload.get("selected_tool")
        attachment_ids = list(payload.get("attachment_ids") or [])
        text = str(payload.get("text_content") or payload.get("content") or "")
        if selected_tool == "schedule":
            return "schedule_intake"
        if selected_tool == "quick_note":
            return "quick_note_intake"
        if attachment_ids:
            return ModelAdapter._fallback_route_workflow(payload)
        if ModelAdapter._looks_like_precise_schedule(text):
            return "schedule_intake"
        if ModelAdapter._looks_like_quick_note(text):
            return "quick_note_intake"
        return "general_chat"

    def route_workflow(self, payload: dict) -> str:
        selected_tool = payload.get("selected_tool")
        if selected_tool == "schedule":
            return "schedule_intake"
        if selected_tool == "quick_note":
            return "quick_note_intake"
        if not self._settings.llm_api_key:
            return self._fallback_route_workflow(payload)
        try:
            result = self._json_completion(
                system_prompt=(
                    "你是 Synora 的工作流路由器，只能输出 JSON。"
                    "字段 workflow 只能是 schedule_intake 或 quick_note_intake。"
                ),
                user_text=json.dumps(
                    {
                        "text_content": payload.get("text_content"),
                        "attachment_ids": payload.get("attachment_ids", []),
                        "context": payload.get("context", {}),
                    },
                    ensure_ascii=False,
                ),
            )
            workflow = str(result.get("workflow") or "").strip()
            if workflow in {"schedule_intake", "quick_note_intake"}:
                return workflow
        except Exception:
            pass
        return self._fallback_route_workflow(payload)

    def route_conversation_intent(self, payload: dict, attachment_parts: list[dict] | None = None) -> str:
        selected_tool = payload.get("selected_tool")
        if selected_tool == "schedule":
            return "schedule_intake"
        if selected_tool == "quick_note":
            return "quick_note_intake"
        if not self._settings.llm_api_key:
            return self._fallback_conversation_intent(payload)
        try:
            result = self._json_completion(
                system_prompt=(
                    "你是 Synora 的对话意图路由器，只能输出 JSON。"
                    "字段 workflow 只能是 schedule_intake、quick_note_intake、general_chat 之一。"
                ),
                user_text=json.dumps(
                    {
                        "text_content": payload.get("text_content"),
                        "attachment_ids": payload.get("attachment_ids", []),
                        "context": payload.get("context", {}),
                    },
                    ensure_ascii=False,
                ),
                attachment_parts=attachment_parts,
            )
            workflow = str(result.get("workflow") or "").strip()
            if workflow in {"schedule_intake", "quick_note_intake", "general_chat"}:
                return workflow
        except Exception:
            pass
        return self._fallback_conversation_intent(payload)

    def extract_schedule(
        self,
        *,
        merged_text: str,
        attachment_parts: list[dict],
        timezone_name: str,
        reference_time: datetime,
    ) -> dict:
        return self._json_completion(
            system_prompt=(
                "你是严格的日程抽取助手。"
                "请从输入内容中抽取一条日程草稿，只输出 JSON。"
                "必须返回字段：title, location, details, is_all_day, start_at, end_at, recurrence, "
                "missing_fields, ambiguity_flags, parse_confidence, evidence_digest。"
                "规则："
                "1. start_at 和 end_at 必须是带时区偏移的 ISO 8601 时间。"
                "2. 如果无法确认具体时间，可返回 null，并在 missing_fields 里写入 start_at 或 end_at。"
                "3. 如果只给出开始时间，默认持续 60 分钟。"
                "4. is_all_day 返回布尔值。"
                "5. recurrence 返回 RRULE 字符串数组，没有重复就返回空数组。"
                "6. evidence_digest 返回 1 到 5 条中文依据。"
                "7. title 使用简洁中文，不超过 30 个字。"
            ),
            user_text=json.dumps(
                {
                    "timezone_name": timezone_name,
                    "reference_time": reference_time.isoformat(),
                    "content": merged_text,
                },
                ensure_ascii=False,
            ),
            attachment_parts=attachment_parts,
        )

    def suggest_quick_note_tags(
        self,
        *,
        merged_text: str,
        manual_tags: list[str],
        attachment_parts: list[dict],
    ) -> dict:
        return self._json_completion(
            system_prompt=(
                "你是速记整理助手，请对输入内容做规范化，只输出 JSON。"
                "必须返回字段：normalized_content, preview_tags, evidence_digest。"
                "规则："
                "1. normalized_content 保留原意，用中文简要整理。"
                "2. preview_tags 返回 2 到 5 个中文标签。"
                "3. 要融合 manual_tags，避免遗漏。"
                "4. evidence_digest 返回 1 到 4 条中文依据。"
            ),
            user_text=json.dumps(
                {
                    "content": merged_text,
                    "manual_tags": manual_tags,
                },
                ensure_ascii=False,
            ),
            attachment_parts=attachment_parts,
        )

    def generate_conversation_title(self, first_message: str) -> str:
        fallback = self._fallback_conversation_title(first_message)
        if not self._settings.llm_api_key:
            return fallback
        try:
            response = self._json_completion(
                system_prompt=(
                    "你是对话标题生成器，只输出 JSON，字段为 title。"
                    "标题要简短、自然、中文，控制在 8 个字以内。"
                ),
                user_text=json.dumps({"first_message": first_message}, ensure_ascii=False),
            )
            title = str(response.get("title") or "").strip()
            if title:
                return title[:18]
        except Exception:
            pass
        return fallback

    def generate_chat_reply(self, *, user_message: str, recent_messages: list[dict[str, str]], attachment_parts: list[dict] | None = None) -> str:
        fallback = self._fallback_chat_reply(user_message)
        if not self._settings.llm_api_key:
            return fallback
        try:
            reply = self._text_completion(
                system_prompt=(
                    "你是 Synora 的中文个人助理，语气自然、简洁、友好。"
                    "如果用户没有明确要求创建日程或速记，就正常对话，不要伪造工具结果。"
                ),
                user_text=json.dumps(
                    {
                        "recent_messages": recent_messages[-6:],
                        "user_message": user_message,
                    },
                    ensure_ascii=False,
                ),
                attachment_parts=attachment_parts,
            )
            return reply or fallback
        except Exception:
            return fallback

    def stream_chat_reply_chunks(
        self,
        *,
        user_message: str,
        recent_messages: list[dict[str, str]],
        attachment_parts: list[dict] | None = None,
    ) -> Iterable[str]:
        fallback = self._fallback_chat_reply(user_message)
        if not self._settings.llm_api_key:
            yield from self._chunk_text(fallback)
            return
        try:
            yielded = False
            for chunk in self.stream_text_completion(
                system_prompt=(
                    "你是 Synora 的中文个人助理，语气自然、简洁、友好。"
                    "如果用户没有明确要求创建日程或速记，就正常对话，不要伪造工具结果。"
                ),
                user_text=json.dumps(
                    {
                        "recent_messages": recent_messages[-6:],
                        "user_message": user_message,
                    },
                    ensure_ascii=False,
                ),
                attachment_parts=attachment_parts,
            ):
                yielded = True
                yield chunk
            if not yielded:
                yield from self._chunk_text(fallback)
        except Exception:
            yield from self._chunk_text(fallback)

    @staticmethod
    def _chunk_text(text: str, chunk_size: int = 12) -> Iterable[str]:
        for index in range(0, len(text), chunk_size):
            yield text[index : index + chunk_size]

    @staticmethod
    def _fallback_conversation_title(first_message: str) -> str:
        cleaned = re.sub(r"\s+", " ", first_message).strip()
        if not cleaned:
            return "新对话"
        return cleaned[:18]

    @staticmethod
    def _fallback_chat_reply(user_message: str) -> str:
        text = user_message.strip()
        if not text:
            return "我在这里，你可以继续告诉我想安排的事情。"
        lower = text.lower()
        if any(keyword in lower for keyword in ["你好", "hi", "hello"]):
            return "你好，我是 Synora。你可以直接告诉我想安排的日程、想保存的速记，或者先和我聊聊。"
        if "谢谢" in text:
            return "不客气，我会继续帮你把事情整理清楚。"
        return "我收到了。你可以继续补充背景，或者直接告诉我需要帮你创建日程、记录速记、查看已有内容。"

    @staticmethod
    def compute_reminder_offsets(start_at: datetime, *, now: datetime | None = None) -> list[int]:
        current = now or datetime.now(ZoneInfo("UTC"))
        delta_minutes = int((start_at - current).total_seconds() // 60)
        if delta_minutes > 1440:
            return [-1440]
        if delta_minutes > 30:
            return [-30]
        return [-5]
