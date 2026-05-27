from __future__ import annotations

import json
import logging
import re
from collections.abc import AsyncIterator, Iterable, Sequence
from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo

from langchain.agents import create_agent
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_openai import ChatOpenAI
from openai import APIConnectionError, APIStatusError, APITimeoutError, AuthenticationError, RateLimitError
from pydantic import BaseModel, Field

from app.config import Settings, get_settings
from app.runtime.errors import LLMServiceError

logger = logging.getLogger(__name__)


class WorkflowSelection(BaseModel):
    workflow: Literal["schedule_intake", "quick_note_intake"]


class ConversationIntentSelection(BaseModel):
    workflow: Literal["schedule_intake", "quick_note_intake", "general_chat"]


class ScheduleExtractionResult(BaseModel):
    title: str = ""
    location: str | None = None
    details: str = ""
    is_all_day: bool = Field(default=False, alias="isAllDay")
    start_at: str | None = None
    end_at: str | None = None
    recurrence: list[str] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)
    ambiguity_flags: list[str] = Field(default_factory=list)
    parse_confidence: float = 0.0
    evidence_digest: list[str] = Field(default_factory=list)


class QuickNotePreparationResult(BaseModel):
    normalized_content: str = ""
    preview_tags: list[str] = Field(default_factory=list)
    evidence_digest: list[str] = Field(default_factory=list)


class ConversationTitleResult(BaseModel):
    title: str


class ModelAdapter:
    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    def _require_api_key(self, *, operation: str) -> None:
        if self._settings.llm_api_key.strip():
            return
        error = LLMServiceError(
            "llm_not_configured",
            "智能服务尚未配置，请检查服务端环境变量后重启容器。",
            retryable=False,
            debug_message=f"LLM api key missing while running {operation}",
        )
        self._log_error(error, operation=operation, streaming=False)
        raise error

    def _is_qwen_model(self) -> bool:
        return "qwen" in self._settings.llm_model.lower()

    def _current_time_prompt(
        self,
        *,
        now: datetime | None = None,
        timezone_name: str | None = None,
    ) -> str:
        resolved_timezone = (timezone_name or self._settings.default_timezone).strip() or self._settings.default_timezone
        try:
            zone = ZoneInfo(resolved_timezone)
        except Exception:
            resolved_timezone = self._settings.default_timezone
            zone = ZoneInfo(resolved_timezone)
        current = (now or datetime.now(zone)).astimezone(zone)
        weekday = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][current.weekday()]
        readable = current.strftime("%Y年%m月%d日 %H:%M")
        return f"当前时区：{resolved_timezone}。当前时间：{current.isoformat()}。当前本地时间：{readable} {weekday}。"

    def _create_chat_model(
        self,
        *,
        temperature: float,
        streaming: bool = False,
        enable_thinking: bool | None = None,
    ) -> ChatOpenAI:
        self._require_api_key(operation="create_chat_model")
        extra_body: dict[str, object] | None = None
        if self._is_qwen_model() and enable_thinking is not None:
            extra_body = {"enable_thinking": bool(enable_thinking)}
        return ChatOpenAI(
            model=self._settings.llm_model,
            api_key=self._settings.llm_api_key,
            base_url=self._settings.llm_base_url,
            timeout=float(self._settings.llm_timeout_seconds),
            temperature=temperature,
            streaming=streaming,
            max_retries=0,
            use_responses_api=False,
            extra_body=extra_body,
        )

    def build_general_chat_agent(self, tools: Sequence[object]):
        model = self._create_chat_model(temperature=0.35, streaming=True, enable_thinking=False)
        return create_agent(
            model=model,
            tools=tools,
            system_prompt=(
                "你是 Synora 的中文智能助理。"
                "回答要自然、准确、简洁。"
                "只有在确实需要时才调用工具；不要伪造工具执行结果。"
                "如果用户只是聊天、提问或咨询，就直接回答。"
                "如果用户请求创建日程或速记，而当前入口已经明确路由到其他流程，则不要在这里重复创建。"
                f"{self._current_time_prompt()}"
            ),
            name="synora_conversation_agent",
        )

    @staticmethod
    def _build_message_content(*, user_text: str, attachment_parts: list[dict] | None = None) -> list[dict]:
        content: list[dict] = []
        if user_text.strip():
            content.append({"type": "text", "text": user_text.strip()})
        content.extend(attachment_parts or [])
        return content or [{"type": "text", "text": "请基于当前输入完成分析。"}]

    @staticmethod
    def _extract_message_text(message: BaseMessage | AIMessage | object) -> str:
        content = getattr(message, "content", message)
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            chunks: list[str] = []
            for item in content:
                if isinstance(item, str):
                    chunks.append(item)
                    continue
                if isinstance(item, dict):
                    if item.get("type") == "text" and isinstance(item.get("text"), str):
                        chunks.append(str(item["text"]))
                        continue
                    if isinstance(item.get("content"), str):
                        chunks.append(str(item["content"]))
                        continue
                text = getattr(item, "text", None)
                if isinstance(text, str):
                    chunks.append(text)
            return "".join(chunks).strip()
        return ""

    def build_langchain_messages(
        self,
        *,
        recent_messages: list[dict[str, str]],
        user_message: str,
        attachment_parts: list[dict] | None = None,
    ) -> list[BaseMessage]:
        messages: list[BaseMessage] = []
        for item in recent_messages:
            role = str(item.get("role") or "")
            content = str(item.get("content") or "").strip()
            if not content:
                continue
            if role == "assistant":
                messages.append(AIMessage(content=content))
            else:
                messages.append(HumanMessage(content=content))
        messages.append(HumanMessage(content=self._build_message_content(user_text=user_message, attachment_parts=attachment_parts)))
        return messages

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
        if isinstance(exc, (json.JSONDecodeError, ValueError, TypeError)):
            return LLMServiceError(
                "llm_invalid_response",
                "智能服务返回异常，本轮未完成。",
                retryable=True,
                debug_message=f"{type(exc).__name__}: {exc}",
            )
        code = "llm_stream_failed" if streaming else "llm_invalid_response"
        message = "本轮回复生成失败，请检查网络后重试。" if streaming else "智能服务返回异常，本轮未完成。"
        return LLMServiceError(code, message, retryable=True, debug_message=f"{type(exc).__name__}: {exc}")

    def _raise_mapped_error(self, exc: Exception, *, operation: str, streaming: bool) -> None:
        mapped = self._map_exception(exc, operation=operation, streaming=streaming)
        self._log_error(mapped, operation=operation, streaming=streaming)
        raise mapped

    def _invoke_structured(
        self,
        *,
        schema: type[BaseModel],
        system_prompt: str,
        user_text: str,
        attachment_parts: list[dict] | None = None,
        operation: str,
    ) -> BaseModel:
        self._require_api_key(operation=operation)
        try:
            model = self._create_chat_model(temperature=0.1, enable_thinking=False)
            structured = model.with_structured_output(schema, method="function_calling")
            result = structured.invoke(
                [
                    ("system", system_prompt),
                    HumanMessage(content=self._build_message_content(user_text=user_text, attachment_parts=attachment_parts)),
                ]
            )
            if isinstance(result, schema):
                return result
            return schema.model_validate(result)
        except Exception as exc:
            self._raise_mapped_error(exc, operation=operation, streaming=False)

    async def _ainvoke_structured(
        self,
        *,
        schema: type[BaseModel],
        system_prompt: str,
        user_text: str,
        attachment_parts: list[dict] | None = None,
        operation: str,
    ) -> BaseModel:
        self._require_api_key(operation=operation)
        try:
            model = self._create_chat_model(temperature=0.1, enable_thinking=False)
            structured = model.with_structured_output(schema, method="function_calling")
            result = await structured.ainvoke(
                [
                    ("system", system_prompt),
                    HumanMessage(content=self._build_message_content(user_text=user_text, attachment_parts=attachment_parts)),
                ]
            )
            if isinstance(result, schema):
                return result
            return schema.model_validate(result)
        except Exception as exc:
            self._raise_mapped_error(exc, operation=operation, streaming=False)

    def _invoke_text(
        self,
        *,
        system_prompt: str,
        user_text: str,
        attachment_parts: list[dict] | None = None,
        operation: str,
    ) -> str:
        self._require_api_key(operation=operation)
        try:
            model = self._create_chat_model(
                temperature=0.35,
                enable_thinking=self._settings.llm_enable_thinking if self._is_qwen_model() else None,
            )
            response = model.invoke(
                [
                    ("system", system_prompt),
                    HumanMessage(content=self._build_message_content(user_text=user_text, attachment_parts=attachment_parts)),
                ]
            )
            content = self._extract_message_text(response)
            if not content:
                raise ValueError("Empty chat completion content")
            return content
        except Exception as exc:
            self._raise_mapped_error(exc, operation=operation, streaming=False)

    async def astream_text(
        self,
        *,
        system_prompt: str,
        user_text: str,
        attachment_parts: list[dict] | None = None,
        operation: str,
    ) -> AsyncIterator[str]:
        self._require_api_key(operation=operation)
        try:
            model = self._create_chat_model(
                temperature=0.35,
                streaming=True,
                enable_thinking=self._settings.llm_enable_thinking if self._is_qwen_model() else None,
            )
            stream = model.astream(
                [
                    ("system", system_prompt),
                    HumanMessage(content=self._build_message_content(user_text=user_text, attachment_parts=attachment_parts)),
                ]
            )
            yielded = False
            async for chunk in stream:
                text = self._extract_message_text(chunk)
                if not text:
                    continue
                yielded = True
                yield text
            if not yielded:
                raise ValueError("Empty streaming response")
        except Exception as exc:
            self._raise_mapped_error(exc, operation=operation, streaming=True)

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
        keywords = ["记一下", "帮我记", "记住", "速记", "备忘", "记录", "存一个", "灵感", "想法", "待办"]
        return any(keyword in text for keyword in keywords)

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
        return "quick_note_intake"

    @staticmethod
    def _fallback_conversation_title(first_message: str) -> str:
        cleaned = re.sub(r"\s+", " ", first_message).strip()
        return cleaned[:18] if cleaned else "新对话"

    def route_workflow(self, payload: dict) -> str:
        selected_tool = payload.get("selected_tool")
        if selected_tool == "schedule":
            return "schedule_intake"
        if selected_tool == "quick_note":
            return "quick_note_intake"
        result = self._invoke_structured(
            schema=WorkflowSelection,
            operation="route_workflow",
            system_prompt=(
                "你是 Synora 的工作流路由器。"
                "只能在 schedule_intake 和 quick_note_intake 中二选一。"
                "如果内容更像带时间地点的安排，就选 schedule_intake。"
                "如果内容更像备忘、灵感、待办或资料整理，就选 quick_note_intake。"
                f"{self._current_time_prompt()}"
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
        return result.workflow

    async def aroute_conversation_intent(self, payload: dict, attachment_parts: list[dict] | None = None) -> str:
        selected_tool = payload.get("selected_tool")
        if selected_tool == "schedule":
            return "schedule_intake"
        if selected_tool == "quick_note":
            return "quick_note_intake"
        result = await self._ainvoke_structured(
            schema=ConversationIntentSelection,
            operation="route_conversation_intent",
            system_prompt=(
                "你是 Synora 的对话路由器。"
                "只能在 general_chat、schedule_intake、quick_note_intake 之间选择。"
                "如果用户在闲聊、提问、咨询建议，就选 general_chat。"
                "如果核心目标是创建可提醒的日程，就选 schedule_intake。"
                "如果核心目标是保存速记、想法、待办或摘要，就选 quick_note_intake。"
                f"{self._current_time_prompt()}"
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
        return result.workflow

    def route_conversation_intent(self, payload: dict, attachment_parts: list[dict] | None = None) -> str:
        selected_tool = payload.get("selected_tool")
        if selected_tool == "schedule":
            return "schedule_intake"
        if selected_tool == "quick_note":
            return "quick_note_intake"
        result = self._invoke_structured(
            schema=ConversationIntentSelection,
            operation="route_conversation_intent",
            system_prompt=(
                "你是 Synora 的对话路由器。"
                "只能在 general_chat、schedule_intake、quick_note_intake 之间选择。"
                "如果用户在闲聊、提问、咨询建议，就选 general_chat。"
                "如果核心目标是创建可提醒的日程，就选 schedule_intake。"
                "如果核心目标是保存速记、想法、待办或摘要，就选 quick_note_intake。"
                f"{self._current_time_prompt()}"
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
        return result.workflow

    def extract_schedule(
        self,
        *,
        merged_text: str,
        attachment_parts: list[dict],
        timezone_name: str,
        reference_time: datetime,
    ) -> dict:
        result = self._invoke_structured(
            schema=ScheduleExtractionResult,
            operation="extract_schedule",
            system_prompt=(
                "你是 Synora 的日程抽取助手。"
                "请从用户原话、附件证据和参考记忆中提取一条待确认的日程草稿。"
                "后续更正优先级高于前文冲突信息。"
                "details 只输出事实摘要，不输出流程话术，不要复述“上一版”“修改为”等表达。"
                "source_text 不由你生成，后端会回填用户原话历史。"
                "start_at 和 end_at 必须输出带时区偏移的 ISO 8601 时间；无法确认时返回 null。"
                "缺失字段只放入 missing_fields，不要猜测。"
                "歧义只放入 ambiguity_flags。"
                "evidence_digest 仅输出中文证据点。"
                f"{self._current_time_prompt(now=reference_time, timezone_name=timezone_name)}"
            ),
            user_text=merged_text,
            attachment_parts=attachment_parts,
        )
        return result.model_dump(mode="json", by_alias=True)

    def suggest_quick_note_tags(
        self,
        *,
        merged_text: str,
        manual_tags: list[str],
        attachment_parts: list[dict],
    ) -> dict:
        result = self._invoke_structured(
            schema=QuickNotePreparationResult,
            operation="suggest_quick_note_tags",
            system_prompt=(
                "你是 Synora 的速记整理助手。"
                "请把输入整理成简洁、自然的中文速记内容，并给出 2 到 5 个中文标签。"
                "尽量保留用户原意，不要过度改写，不要扩展不存在的信息。"
                "必须融合 manual_tags，不要遗漏用户手动指定的标签。"
                "evidence_digest 只输出中文依据。"
                f"{self._current_time_prompt()}"
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
        return result.model_dump(mode="json")

    def generate_conversation_title(self, first_message: str) -> str:
        fallback = self._fallback_conversation_title(first_message)
        if not self._settings.llm_api_key:
            return fallback
        try:
            result = self._invoke_structured(
                schema=ConversationTitleResult,
                operation="generate_conversation_title",
                system_prompt=(
                    "你是 Synora 的对话标题生成器。"
                    "请基于用户首条消息生成一个 8 到 18 个字以内的简短中文标题。"
                    f"{self._current_time_prompt()}"
                ),
                user_text=first_message,
            )
            title = result.title.strip()
            return title[:18] if title else fallback
        except LLMServiceError:
            return fallback

    def generate_chat_reply(
        self,
        *,
        user_message: str,
        recent_messages: list[dict[str, str]],
        attachment_parts: list[dict] | None = None,
    ) -> str:
        return self._invoke_text(
            operation="generate_chat_reply",
            system_prompt=(
                "你是 Synora 的中文助理。"
                "在没有明确工具任务时，直接自然回答即可。"
                f"{self._current_time_prompt()}"
            ),
            user_text=json.dumps(
                {
                    "recent_messages": recent_messages[-8:],
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
        async def _run() -> list[str]:
            chunks: list[str] = []
            async for chunk in self.astream_text(
                operation="stream_chat_reply_chunks",
                system_prompt=(
                    "你是 Synora 的中文助理。"
                    "在没有明确工具任务时，直接自然回答即可。"
                    f"{self._current_time_prompt()}"
                ),
                user_text=json.dumps(
                    {
                        "recent_messages": recent_messages[-8:],
                        "user_message": user_message,
                    },
                    ensure_ascii=False,
                ),
                attachment_parts=attachment_parts,
            ):
                chunks.append(chunk)
            return chunks

        import asyncio

        for item in asyncio.run(_run()):
            yield item

    @staticmethod
    def compute_reminder_offsets(start_at: datetime, *, now: datetime | None = None) -> list[int]:
        current = now or datetime.now(ZoneInfo("UTC"))
        delta_minutes = int((start_at - current).total_seconds() // 60)
        if delta_minutes > 1440:
            return [-1440]
        if delta_minutes > 30:
            return [-30]
        return [-5]
