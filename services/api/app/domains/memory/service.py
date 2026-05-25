from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.models import MemoryProfile, MemoryRecord
from app.runtime.model_adapter import ModelAdapter
from app.security import sha256_text

logger = logging.getLogger(__name__)

try:
    from llama_index.core import StorageContext, VectorStoreIndex
    from llama_index.core.schema import TextNode
    from llama_index.core.vector_stores.types import MetadataFilter, MetadataFilters
    from llama_index.embeddings.openai_like import OpenAILikeEmbedding
    from llama_index.vector_stores.postgres import PGVectorStore
except Exception:  # pragma: no cover - import guarded for environments without deps
    StorageContext = None
    VectorStoreIndex = None
    TextNode = None
    MetadataFilter = None
    MetadataFilters = None
    OpenAILikeEmbedding = None
    PGVectorStore = None


MEMORY_TYPE_VALUES = {
    "preference",
    "constraint",
    "profile_fact",
    "confirmed_schedule",
    "confirmed_quick_note",
}


@dataclass
class MemoryContext:
    summary: str
    items: list[dict[str, Any]]


class MemoryService:
    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    def is_enabled(self) -> bool:
        return bool(
            self._settings.memory_enabled
            and self._settings.llm_api_key.strip()
            and StorageContext is not None
            and VectorStoreIndex is not None
            and PGVectorStore is not None
            and OpenAILikeEmbedding is not None
        )

    def retrieve_context(
        self,
        db: Session,
        *,
        user_id: int,
        query_text: str,
        limit: int | None = None,
    ) -> MemoryContext:
        if db is None:
            return MemoryContext(summary="", items=[])
        profile = db.scalar(select(MemoryProfile).where(MemoryProfile.user_id == user_id))
        summary = profile.summary_text if profile else ""
        if not self.is_enabled() or not query_text.strip():
            return MemoryContext(summary=summary, items=[])

        try:
            retriever = self._build_index().as_retriever(
                similarity_top_k=limit or self._settings.memory_top_k,
                filters=MetadataFilters(
                    filters=[
                        MetadataFilter(key="user_id", value=user_id),
                        MetadataFilter(key="is_active", value=True),
                    ]
                ),
            )
            nodes = retriever.retrieve(query_text.strip())
            items: list[dict[str, Any]] = []
            for node in nodes:
                metadata = dict(getattr(node, "metadata", {}) or {})
                content = getattr(node, "text", None) or getattr(node, "get_content", lambda: "")()
                memory_id = metadata.get("memory_record_id")
                if memory_id is None:
                    continue
                items.append(
                    {
                        "id": int(memory_id),
                        "memory_type": str(metadata.get("memory_type") or "profile_fact"),
                        "title": str(metadata.get("title") or "长期记忆"),
                        "content": str(content or "").strip(),
                        "source_kind": str(metadata.get("source_kind") or ""),
                        "source_ref_id": metadata.get("source_ref_id"),
                    }
                )
            return MemoryContext(summary=summary, items=items)
        except Exception as exc:  # pragma: no cover - degrade on infra issues
            logger.warning("memory_retrieval_failed detail=%s", exc)
            return MemoryContext(summary=summary, items=[])

    def extract_memory_facts(
        self,
        *,
        text: str,
        summary: str = "",
    ) -> list[dict[str, str]]:
        cleaned = text.strip()
        if not cleaned:
            return []

        lowered = cleaned.lower()
        memory_type = "profile_fact"
        title = "用户长期事实"
        if any(keyword in cleaned for keyword in ("偏好", "喜欢", "习惯", "通常", "总是", "提醒我")):
            memory_type = "preference"
            title = "用户偏好"
        elif any(keyword in cleaned for keyword in ("不要", "避免", "不能", "别在", "限制")):
            memory_type = "constraint"
            title = "用户约束"

        if "schedule_saved" in lowered or "quick_note_saved" in lowered:
            memory_type = "profile_fact"

        return [{"memory_type": memory_type, "title": title, "content": cleaned, "summary": summary}]

    def upsert_memory_records(
        self,
        db: Session,
        *,
        user_id: int,
        source_kind: str,
        source_ref_id: str | None,
        entries: list[dict[str, str]],
    ) -> list[MemoryRecord]:
        if not entries:
            return []

        saved: list[MemoryRecord] = []
        now = datetime.now(timezone.utc)
        for entry in entries:
            memory_type = str(entry.get("memory_type") or "profile_fact")
            if memory_type not in MEMORY_TYPE_VALUES:
                memory_type = "profile_fact"
            content = str(entry.get("content") or "").strip()
            if not content:
                continue
            title = str(entry.get("title") or "长期记忆").strip()[:255] or "长期记忆"
            normalized_hash = sha256_text(f"{user_id}:{memory_type}:{content}")
            record = db.scalar(
                select(MemoryRecord).where(
                    MemoryRecord.user_id == user_id,
                    MemoryRecord.normalized_hash == normalized_hash,
                )
            )
            if record is None:
                record = MemoryRecord(
                    user_id=user_id,
                    memory_type=memory_type,
                    title=title,
                    content=content,
                    source_kind=source_kind,
                    source_ref_id=source_ref_id,
                    vector_node_id=f"memory-{uuid4().hex}",
                    normalized_hash=normalized_hash,
                    is_active=True,
                )
                db.add(record)
                db.flush()
            else:
                record.title = title
                record.content = content
                record.source_kind = source_kind
                record.source_ref_id = source_ref_id
                record.is_active = True
                record.updated_at = now
                if not record.vector_node_id:
                    record.vector_node_id = f"memory-{uuid4().hex}"
            saved.append(record)
        db.commit()
        for item in saved:
            db.refresh(item)

        if self.is_enabled():
            try:
                self._upsert_vector_nodes(saved)
            except Exception as exc:  # pragma: no cover - infra degradation
                logger.warning("memory_vector_upsert_failed detail=%s", exc)
        self._refresh_profile_summary(db, user_id=user_id)
        return saved

    def delete_record(self, db: Session, *, user_id: int, memory_id: int) -> None:
        record = db.scalar(
            select(MemoryRecord).where(
                MemoryRecord.id == memory_id,
                MemoryRecord.user_id == user_id,
            )
        )
        if record is None:
            raise ValueError("记忆不存在或已删除。")
        node_id = record.vector_node_id
        db.delete(record)
        db.commit()
        if self.is_enabled() and node_id:
            try:
                self._get_vector_store().delete_nodes(node_ids=[node_id])
            except Exception as exc:  # pragma: no cover
                logger.warning("memory_vector_delete_failed detail=%s", exc)
        self._refresh_profile_summary(db, user_id=user_id)

    def clear_user_memory(self, db: Session, *, user_id: int) -> None:
        records = db.scalars(select(MemoryRecord).where(MemoryRecord.user_id == user_id)).all()
        node_ids = [item.vector_node_id for item in records if item.vector_node_id]
        for record in records:
            db.delete(record)
        profile = db.scalar(select(MemoryProfile).where(MemoryProfile.user_id == user_id))
        if profile:
            profile.summary_text = ""
        db.commit()
        if self.is_enabled() and node_ids:
            try:
                self._get_vector_store().delete_nodes(node_ids=node_ids)
            except Exception as exc:  # pragma: no cover
                logger.warning("memory_vector_clear_failed detail=%s", exc)

    def list_records(self, db: Session, *, user_id: int) -> tuple[str, list[MemoryRecord]]:
        profile = db.scalar(select(MemoryProfile).where(MemoryProfile.user_id == user_id))
        records = db.scalars(
            select(MemoryRecord)
            .where(MemoryRecord.user_id == user_id, MemoryRecord.is_active.is_(True))
            .order_by(MemoryRecord.updated_at.desc(), MemoryRecord.id.desc())
        ).all()
        return (profile.summary_text if profile else "", list(records))

    def _refresh_profile_summary(self, db: Session, *, user_id: int) -> None:
        records = db.scalars(
            select(MemoryRecord)
            .where(MemoryRecord.user_id == user_id, MemoryRecord.is_active.is_(True))
            .order_by(MemoryRecord.updated_at.desc(), MemoryRecord.id.desc())
            .limit(20)
        ).all()
        if not records:
            profile = db.scalar(select(MemoryProfile).where(MemoryProfile.user_id == user_id))
            if profile:
                profile.summary_text = ""
                db.commit()
            return

        lines = [f"{item.title}：{item.content}" for item in records[:8]]
        summary = "\n".join(lines)
        if self._settings.llm_api_key.strip():
            try:
                summary = ModelAdapter(self._settings)._invoke_text(
                    operation="summarize_memory_profile",
                    system_prompt=(
                        "你是 Synora 的用户画像整理助手。"
                        "请把输入整理成 80 到 160 字的中文摘要，只保留稳定偏好、约束、常用场景和近期确认过的重要事实。"
                    ),
                    user_text=summary,
                ).strip()
            except Exception as exc:  # pragma: no cover
                logger.warning("memory_profile_summary_failed detail=%s", exc)
        profile = db.scalar(select(MemoryProfile).where(MemoryProfile.user_id == user_id))
        if profile is None:
            profile = MemoryProfile(user_id=user_id, summary_text=summary)
            db.add(profile)
        else:
            profile.summary_text = summary
        db.commit()

    def _upsert_vector_nodes(self, records: list[MemoryRecord]) -> None:
        vector_store = self._get_vector_store()
        nodes = [
            TextNode(
                id_=record.vector_node_id,
                text=record.content,
                metadata={
                    "user_id": record.user_id,
                    "memory_record_id": record.id,
                    "memory_type": record.memory_type,
                    "title": record.title,
                    "source_kind": record.source_kind,
                    "source_ref_id": record.source_ref_id or "",
                    "is_active": record.is_active,
                },
            )
            for record in records
            if record.vector_node_id
        ]
        if nodes:
            vector_store.add(nodes)

    def _build_index(self) -> VectorStoreIndex:
        vector_store = self._get_vector_store()
        return VectorStoreIndex.from_vector_store(vector_store=vector_store, embed_model=self._get_embed_model())

    def _get_embed_model(self):
        return OpenAILikeEmbedding(
            model_name=self._settings.memory_embedding_model,
            api_key=self._settings.llm_api_key,
            api_base=self._settings.llm_base_url,
            timeout=float(self._settings.llm_timeout_seconds),
            dimensions=self._settings.memory_embedding_dimensions,
            max_retries=0,
        )

    def _get_vector_store(self) -> PGVectorStore:
        return PGVectorStore(
            connection_string=self._settings.database_url.replace("+psycopg", ""),
            async_connection_string=self._settings.database_url.replace("+psycopg", "+asyncpg"),
            table_name=self._settings.memory_vector_table,
            schema_name=self._settings.memory_vector_schema,
            embed_dim=self._settings.memory_embedding_dimensions,
            perform_setup=True,
            use_jsonb=True,
            initialization_fail_on_error=False,
        )
