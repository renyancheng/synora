from __future__ import annotations

import logging
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.models import ConversationMessage

logger = logging.getLogger(__name__)

try:
    from llama_index.core import VectorStoreIndex
    from llama_index.core.schema import TextNode
    from llama_index.core.vector_stores.types import MetadataFilter, MetadataFilters
    from llama_index.embeddings.openai_like import OpenAILikeEmbedding
    from llama_index.vector_stores.postgres import PGVectorStore
except Exception:  # pragma: no cover - import guarded for environments without deps
    VectorStoreIndex = None
    TextNode = None
    MetadataFilter = None
    MetadataFilters = None
    OpenAILikeEmbedding = None
    PGVectorStore = None


class ConversationHistorySearchService:
    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    def is_enabled(self) -> bool:
        return bool(
            self._settings.semantic_search_enabled
            and self._settings.llm_api_key.strip()
            and VectorStoreIndex is not None
            and PGVectorStore is not None
            and OpenAILikeEmbedding is not None
            and TextNode is not None
            and MetadataFilter is not None
            and MetadataFilters is not None
        )

    def upsert_message(self, message: ConversationMessage) -> None:
        if not self.is_enabled():
            return
        if message.message_type != "text":
            return
        text = str(message.text_content or "").strip()
        if not text:
            return
        try:
            self._replace_nodes(
                nodes=[
                    TextNode(
                        id_=self.message_node_id(
                            conversation_id=message.conversation_id,
                            message_id=message.id,
                        ),
                        text=text,
                        metadata={
                            "user_id": message.conversation.user_id,
                            "conversation_id": message.conversation_id,
                            "message_id": message.id,
                            "role": message.role,
                            "created_at": message.created_at.isoformat(),
                        },
                    )
                ]
            )
        except Exception as exc:  # pragma: no cover - infra degradation
            logger.warning(
                "conversation_history_vector_upsert_failed conversation_id=%s message_id=%s detail=%s",
                message.conversation_id,
                message.id,
                exc,
            )

    def delete_messages(self, *, conversation_id: int, message_ids: Iterable[int]) -> None:
        if PGVectorStore is None:
            return
        node_ids = [
            self.message_node_id(conversation_id=conversation_id, message_id=message_id)
            for message_id in message_ids
            if isinstance(message_id, int)
        ]
        if not node_ids:
            return
        try:
            self._get_vector_store().delete_nodes(node_ids=node_ids)
        except Exception as exc:  # pragma: no cover - infra degradation
            logger.warning(
                "conversation_history_vector_delete_failed conversation_id=%s detail=%s",
                conversation_id,
                exc,
            )

    def retrieve_history_lines(
        self,
        db: Session,
        *,
        conversation_id: int,
        current_user_message_id: int | None,
        recent_window: int,
    ) -> list[str]:
        if db is None or not self.is_enabled() or not current_user_message_id:
            return []
        current_message = db.get(ConversationMessage, current_user_message_id)
        query_text = str(getattr(current_message, "text_content", "") or "").strip()
        if not query_text:
            return []

        recent_rows = db.scalars(
            select(ConversationMessage)
            .where(
                ConversationMessage.conversation_id == conversation_id,
                ConversationMessage.message_type == "text",
                ConversationMessage.id != current_user_message_id,
            )
            .order_by(ConversationMessage.created_at.desc(), ConversationMessage.id.desc())
            .limit(recent_window)
        ).all()
        excluded_ids = {row.id for row in recent_rows}
        excluded_ids.add(current_user_message_id)

        message_ids = self._search_message_ids(
            user_id=current_message.conversation.user_id,
            conversation_id=conversation_id,
            query_text=query_text,
            limit=self._settings.conversation_history_candidate_limit,
        )
        filtered_ids = [message_id for message_id in message_ids if message_id not in excluded_ids]
        if not filtered_ids:
            return []

        rows = db.scalars(
            select(ConversationMessage).where(
                ConversationMessage.conversation_id == conversation_id,
                ConversationMessage.id.in_(filtered_ids),
                ConversationMessage.message_type == "text",
            )
        ).all()
        by_id = {row.id: row for row in rows if str(row.text_content or "").strip()}
        selected_rows = [by_id[message_id] for message_id in filtered_ids if message_id in by_id]
        if not selected_rows:
            return []

        selected_rows.sort(key=lambda item: (item.created_at, item.id))
        lines = [
            self._format_history_line(
                role=row.role,
                text=str(row.text_content or ""),
            )
            for row in selected_rows[: self._settings.conversation_history_top_k]
        ]
        return [line for line in lines if line]

    @staticmethod
    def message_node_id(*, conversation_id: int, message_id: int) -> str:
        return f"conversation-message:{conversation_id}:{message_id}"

    def _search_message_ids(
        self,
        *,
        user_id: int,
        conversation_id: int,
        query_text: str,
        limit: int,
    ) -> list[int]:
        if not query_text.strip() or not self.is_enabled():
            return []
        try:
            retriever = self._build_index().as_retriever(
                similarity_top_k=limit,
                filters=MetadataFilters(
                    filters=[
                        MetadataFilter(key="user_id", value=user_id),
                        MetadataFilter(key="conversation_id", value=conversation_id),
                    ]
                ),
            )
            nodes = retriever.retrieve(query_text.strip())
            ordered_ids: list[int] = []
            for node in nodes:
                metadata = dict(getattr(node, "metadata", {}) or {})
                raw_id = metadata.get("message_id")
                if raw_id is None:
                    continue
                try:
                    message_id = int(raw_id)
                except (TypeError, ValueError):
                    continue
                if message_id not in ordered_ids:
                    ordered_ids.append(message_id)
            return ordered_ids
        except Exception as exc:  # pragma: no cover - infra degradation
            logger.warning(
                "conversation_history_search_failed conversation_id=%s detail=%s",
                conversation_id,
                exc,
            )
            return []

    def _replace_nodes(self, *, nodes: list[TextNode]) -> None:
        if not nodes:
            return
        vector_store = self._get_vector_store()
        node_ids = [node.node_id for node in nodes if node.node_id]
        if node_ids:
            try:
                vector_store.delete_nodes(node_ids=node_ids)
            except Exception:
                pass
        vector_store.add(nodes)

    def _build_index(self) -> VectorStoreIndex:
        vector_store = self._get_vector_store()
        return VectorStoreIndex.from_vector_store(
            vector_store=vector_store,
            embed_model=self._get_embed_model(),
        )

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
            table_name=self._settings.conversation_history_vector_table,
            schema_name=self._settings.conversation_history_vector_schema,
            embed_dim=self._settings.memory_embedding_dimensions,
            perform_setup=True,
            use_jsonb=True,
            initialization_fail_on_error=False,
        )

    @staticmethod
    def _format_history_line(*, role: str, text: str, limit: int = 140) -> str:
        normalized = " ".join(text.strip().split())
        if not normalized:
            return ""
        if len(normalized) > limit:
            normalized = normalized[: limit - 1].rstrip() + "…"
        speaker = "用户" if role == "user" else "助手"
        return f"{speaker}：{normalized}"
