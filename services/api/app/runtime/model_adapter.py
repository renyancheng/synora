from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from typing import Iterable
from zoneinfo import ZoneInfo

from openai import APIConnectionError, APIStatusError, APITimeoutError, AuthenticationError, OpenAI, RateLimitError

from app.config import Settings, get_settings
from app.runtime.errors import LLMServiceError

logger = logging.getLogger(__name__)


class ModelAdapter:
    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._client = OpenAI(
            api_key=self._settings.llm_api_key,
            base_url=self._settings.llm_base_url,
            timeout=self._settings.llm_timeout_seconds,
        )

    def _require_api_key(self, *, operation: str) -> None:
        if not self._settings.llm_api_key:
            error = LLMServiceError(
                "llm_not_configured",
                "智能服务尚未配置，请检查服务端环境变量后重启容器。",
                retryable=False,
                debug_message=f"LLM api key missing while running {operation}",
            )
            self._log_error(error, operation=operation, streaming=False)
            raise error

    def _build_message_content(self, *, user_text: str, attachment_parts: list[dict] | None = None) -> list[dict]:
        content: list[dict] = []
        if user_text.strip():
            content.append({"type": "text", "text": user_text.strip()})
        content.extend(attachment_parts or [])
        return content or [{"type": "text", "text": "请基于当前输入完成分析。"}]

    def _log_error(self, error: LLMServiceError, *, operation: str, streaming: bool) -> None:
        logger.warning(
            "llm_request_failed code=%s operation=%s streaming=%s model=%s base_url=%s detail=%s",
            error.code,
            operation,
            streaming,
            self._settings.llm_model,
            self._settings.llm_base_url,
            error.debug_message,
        )

    def _map_exception(self, exc: Exception, *, operation: str, streaming: bool) -> LLMServiceError:
        if isinstance(exc, LLMServiceError):
            return exc
        if isinstance(exc, AuthenticationError):
            return LLMServiceError(
                "llm_auth_failed",
                "智能服务鉴权失败，请检查模型密钥是否有效。",
                retryable=False,
                debug_message=f"{type(exc).__name__}: {exc}",
            )
        if isinstance(exc, RateLimitError):
            return LLMServiceError(
                "llm_rate_limited",
                "当前请求较多，稍后再试。",
                retryable=True,
                debug_message=f"{type(exc).__name__}: {exc}",
            )
        if isinstance(exc, APITimeoutError):
            return LLMServiceError(
                "llm_timeout",
                "本轮回复生成失败，请检查网络后重试。",
                retryable=True,
                debug_message=f"{type(exc).__name__}: {exc}",
            )
        if isinstance(exc, APIConnectionError):
            return LLMServiceError(
                "llm_network_error",
                "本轮回复生成失败，请检查网络后重试。",
                retryable=True,
                debug_message=f"{type(exc).__name__}: {exc}",
            )
        if isinstance(exc, APIStatusError):
            if exc.status_code == 401:
                return LLMServiceError(
                    "llm_auth_failed",
                    "智能服务鉴权失败，请检查模型密钥是否有效。",
                    retryable=False,
                    debug_message=f"{type(exc).__name__}: status={exc.status_code} body={exc.body}",
                )
            if exc.status_code == 429:
                return LLMServiceError(
                    "llm_rate_limited",
                    "当前请求较多，稍后再试。",
                    retryable=True,
                    debug_message=f"{type(exc).__name__}: status={exc.status_code} body={exc.body}",
                )
            return LLMServiceError(
                "llm_invalid_response",
                "智能服务返回异常，本轮未完成。",
                retryable=True,
                debug_message=f"{type(exc).__name__}: status={exc.status_code} body={exc.body}",
            )
        if isinstance(exc, json.JSONDecodeError):
            return LLMServiceError(
                "llm_invalid_response",
                "智能服务返回异常，本轮未完成。",
                retryable=True,
                debug_message=f"{type(exc).__name__}: {exc}",
            )
        code = "llm_stream_failed" if streaming else "llm_invalid_response"
        message = "本轮回复生成失败，请检查网络后重试。" if streaming else "智能服务返回异常，本轮未完成。"
        return LLMServiceError(
            code,
            message,
            retryable=True,
            debug_message=f"{type(exc).__name__}: {exc}",
        )

    def _raise_mapped_error(self, exc: Exception, *, operation: str, streaming: bool) -> None:
        mapped = self._map_exception(exc, operation=operation, streaming=streaming)
        self._log_error(mapped, operation=operation, streaming=streaming)
        raise mapped

    def _json_completion(
        self,
        *,
        system_prompt: str,
        user_text: str,
        attachment_parts: list[dict] | None = None,
        operation: str,
    ) -> dict:
        self._require_api_key(operation=operation)
        try:
            response = self._client.chat.completions.create(
                model=self._settings.llm_model,
                temperature=0.1,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": self._build_message_content(user_text=user_text, attachment_parts=attachment_parts)},
                ],
            )
            content = response.choices[0].message.content or ""
            if not content.strip():
                raise ValueError("Empty JSON completion content")
            return json.loads(content)
        except Exception as exc:
            self._raise_mapped_error(exc, operation=operation, streaming=False)

    def _text_completion(
        self,
        *,
        system_prompt: str,
        user_text: str,
        attachment_parts: list[dict] | None = None,
        operation: str,
    ) -> str:
        self._require_api_key(operation=operation)
        try:
            response = self._client.chat.completions.create(
                model=self._settings.llm_model,
                temperature=0.3,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": self._build_message_content(user_text=user_text, attachment_parts=attachment_parts)},
                ],
            )
            content = (response.choices[0].message.content or "").strip()
            if not content:
                raise ValueError("Empty text completion content")
            return content
        except Exception as exc:
            self._raise_mapped_error(exc, operation=operation, streaming=False)

    def stream_text_completion(
        self,
        *,
        system_prompt: str,
        user_text: str,
        attachment_parts: list[dict] | None = None,
        operation: str,
    ) -> Iterable[str]:
        self._require_api_key(operation=operation)
        try:
            stream = self._client.chat.completions.create(
                model=self._settings.llm_model,
                temperature=0.3,
                stream=True,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": self._build_message_content(user_text=user_text, attachment_parts=attachment_parts)},
                ],
            )
        except Exception as exc:
            self._raise_mapped_error(exc, operation=operation, streaming=True)

        yielded = False
        try:
            for chunk in stream:
                choices = getattr(chunk, "choices", None) or []
                if not choices:
                    continue
                delta = choices[0].delta.content or ""
                if delta:
                    yielded = True
                    yield delta
        except Exception as exc:
            self._raise_mapped_error(exc, operation=operation, streaming=True)

        if not yielded:
            error = LLMServiceError(
                "llm_stream_failed",
                "本轮回复生成失败，请检查网络后重试。",
                retryable=True,
                debug_message=f"Empty streaming response while running {operation}",
            )
            self._log_error(error, operation=operation, streaming=True)
            raise error

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
        explicit_keywords = ["记一下", "帮我记", "记住", "速记", "备忘", "记录一个", "存一个", "灵感", "想法", "待办"]
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
    def _fallback_conversation_title(first_message: str) -> str:
        cleaned = re.sub(r"\s+", " ", first_message).strip()
        if not cleaned:
            return "新对话"
        return cleaned[:18]

    def route_workflow(self, payload: dict) -> str:
        selected_tool = payload.get("selected_tool")
        if selected_tool == "schedule":
            return "schedule_intake"
        if selected_tool == "quick_note":
            return "quick_note_intake"
        result = self._json_completion(
            operation="route_workflow",
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
        self._raise_mapped_error(ValueError(f"Unexpected workflow: {workflow}"), operation="route_workflow", streaming=False)

    def route_conversation_intent(self, payload: dict, attachment_parts: list[dict] | None = None) -> str:
        selected_tool = payload.get("selected_tool")
        if selected_tool == "schedule":
            return "schedule_intake"
        if selected_tool == "quick_note":
            return "quick_note_intake"
        result = self._json_completion(
            operation="route_conversation_intent",
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
        self._raise_mapped_error(ValueError(f"Unexpected workflow: {workflow}"), operation="route_conversation_intent", streaming=False)

    def extract_schedule(
        self,
        *,
        merged_text: str,
        attachment_parts: list[dict],
        timezone_name: str,
        reference_time: datetime,
    ) -> dict:
        return self._json_completion(
            operation="extract_schedule",
            system_prompt=(
                "你是严格的日程抽取助手。"
                "请从输入内容中抽取一条日程草稿，只输出 JSON。"
                "必须返回字段：title, location, details, is_all_day, start_at, end_at, recurrence, "
                "missing_fields, ambiguity_flags, parse_confidence, evidence_digest。"
                "规则："
                "1. start_at 和 end_at 必须是带时区偏移的 ISO 8601 时间。"
                "2. 如果无法确认具体时间，可返回 null，并在 missing_fields 中写入 start_at 或 end_at。"
                "3. 如果只有开始时间，默认持续 60 分钟。"
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
            operation="suggest_quick_note_tags",
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
                operation="generate_conversation_title",
                system_prompt=(
                    "你是对话标题生成器，只输出 JSON，字段为 title。"
                    "标题要简短、自然、中文，控制在 8 个字以内。"
                ),
                user_text=json.dumps({"first_message": first_message}, ensure_ascii=False),
            )
            title = str(response.get("title") or "").strip()
            return title[:18] if title else fallback
        except LLMServiceError:
            return fallback

    def generate_chat_reply(self, *, user_message: str, recent_messages: list[dict[str, str]], attachment_parts: list[dict] | None = None) -> str:
        return self._text_completion(
            operation="generate_chat_reply",
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

    def stream_chat_reply_chunks(
        self,
        *,
        user_message: str,
        recent_messages: list[dict[str, str]],
        attachment_parts: list[dict] | None = None,
    ) -> Iterable[str]:
        yield from self.stream_text_completion(
            operation="stream_chat_reply_chunks",
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

    @staticmethod
    def compute_reminder_offsets(start_at: datetime, *, now: datetime | None = None) -> list[int]:
        current = now or datetime.now(ZoneInfo("UTC"))
        delta_minutes = int((start_at - current).total_seconds() // 60)
        if delta_minutes > 1440:
            return [-1440]
        if delta_minutes > 30:
            return [-30]
        return [-5]
