from __future__ import annotations

import logging

from app.config import Settings, get_settings
from app.models import QuickNote, Schedule

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


class SemanticSearchService:
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

    def search_quick_note_ids(self, *, user_id: int, query_text: str, limit: int = 40) -> list[int]:
        return self._retrieve_entity_ids(
            query_text=query_text,
            user_id=user_id,
            table_name=self._settings.quick_note_vector_table,
            schema_name=self._settings.quick_note_vector_schema,
            metadata_key="note_id",
            limit=limit,
        )

    def search_schedule_ids(self, *, user_id: int, query_text: str, limit: int = 40) -> list[int]:
        return self._retrieve_entity_ids(
            query_text=query_text,
            user_id=user_id,
            table_name=self._settings.schedule_vector_table,
            schema_name=self._settings.schedule_vector_schema,
            metadata_key="schedule_id",
            limit=limit,
        )

    def upsert_quick_note(self, note: QuickNote) -> None:
        if not self.is_enabled():
            return
        node_id = self.quick_note_node_id(user_id=note.user_id, note_id=note.id)
        text = self._quick_note_text(note)
        try:
            self._replace_nodes(
                table_name=self._settings.quick_note_vector_table,
                schema_name=self._settings.quick_note_vector_schema,
                nodes=[
                    TextNode(
                        id_=node_id,
                        text=text,
                        metadata={
                            "user_id": note.user_id,
                            "note_id": note.id,
                            "tags": list(note.topic_tags_json or []),
                        },
                    )
                ],
            )
        except Exception as exc:  # pragma: no cover - infra degradation
            logger.warning("quick_note_vector_upsert_failed note_id=%s detail=%s", note.id, exc)

    def delete_quick_note(self, *, user_id: int, note_id: int) -> None:
        self._delete_node(
            table_name=self._settings.quick_note_vector_table,
            schema_name=self._settings.quick_note_vector_schema,
            node_id=self.quick_note_node_id(user_id=user_id, note_id=note_id),
            log_prefix="quick_note_vector_delete_failed",
        )

    def upsert_schedule(self, schedule: Schedule) -> None:
        if not self.is_enabled():
            return
        node_id = self.schedule_node_id(user_id=schedule.user_id, schedule_id=schedule.id)
        text = self._schedule_text(schedule)
        try:
            self._replace_nodes(
                table_name=self._settings.schedule_vector_table,
                schema_name=self._settings.schedule_vector_schema,
                nodes=[
                    TextNode(
                        id_=node_id,
                        text=text,
                        metadata={
                            "user_id": schedule.user_id,
                            "schedule_id": schedule.id,
                            "status": schedule.status,
                        },
                    )
                ],
            )
        except Exception as exc:  # pragma: no cover - infra degradation
            logger.warning("schedule_vector_upsert_failed schedule_id=%s detail=%s", schedule.id, exc)

    def delete_schedule(self, *, user_id: int, schedule_id: int) -> None:
        self._delete_node(
            table_name=self._settings.schedule_vector_table,
            schema_name=self._settings.schedule_vector_schema,
            node_id=self.schedule_node_id(user_id=user_id, schedule_id=schedule_id),
            log_prefix="schedule_vector_delete_failed",
        )

    @staticmethod
    def quick_note_node_id(*, user_id: int, note_id: int) -> str:
        return f"quick-note:{user_id}:{note_id}"

    @staticmethod
    def schedule_node_id(*, user_id: int, schedule_id: int) -> str:
        return f"schedule:{user_id}:{schedule_id}"

    def _retrieve_entity_ids(
        self,
        *,
        query_text: str,
        user_id: int,
        table_name: str,
        schema_name: str,
        metadata_key: str,
        limit: int,
    ) -> list[int]:
        cleaned = query_text.strip()
        if not cleaned or not self.is_enabled():
            return []
        try:
            retriever = self._build_index(table_name=table_name, schema_name=schema_name).as_retriever(
                similarity_top_k=limit,
                filters=MetadataFilters(filters=[MetadataFilter(key="user_id", value=user_id)]),
            )
            nodes = retriever.retrieve(cleaned)
            ordered_ids: list[int] = []
            for node in nodes:
                metadata = dict(getattr(node, "metadata", {}) or {})
                raw_id = metadata.get(metadata_key)
                if raw_id is None:
                    continue
                try:
                    entity_id = int(raw_id)
                except (TypeError, ValueError):
                    continue
                if entity_id not in ordered_ids:
                    ordered_ids.append(entity_id)
            return ordered_ids
        except Exception as exc:  # pragma: no cover - infra degradation
            logger.warning("semantic_search_failed table=%s detail=%s", table_name, exc)
            return []

    def _replace_nodes(self, *, table_name: str, schema_name: str, nodes: list[TextNode]) -> None:
        if not nodes:
            return
        vector_store = self._get_vector_store(table_name=table_name, schema_name=schema_name)
        node_ids = [node.node_id for node in nodes if node.node_id]
        if node_ids:
            try:
                vector_store.delete_nodes(node_ids=node_ids)
            except Exception:
                pass
        vector_store.add(nodes)

    def _delete_node(self, *, table_name: str, schema_name: str, node_id: str, log_prefix: str) -> None:
        if PGVectorStore is None:
            return
        try:
            self._get_vector_store(table_name=table_name, schema_name=schema_name).delete_nodes(node_ids=[node_id])
        except Exception as exc:  # pragma: no cover - infra degradation
            logger.warning("%s node_id=%s detail=%s", log_prefix, node_id, exc)

    def _build_index(self, *, table_name: str, schema_name: str) -> VectorStoreIndex:
        vector_store = self._get_vector_store(table_name=table_name, schema_name=schema_name)
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

    def _get_vector_store(self, *, table_name: str, schema_name: str) -> PGVectorStore:
        return PGVectorStore(
            connection_string=self._settings.database_url.replace("+psycopg", ""),
            async_connection_string=self._settings.database_url.replace("+psycopg", "+asyncpg"),
            table_name=table_name,
            schema_name=schema_name,
            embed_dim=self._settings.memory_embedding_dimensions,
            perform_setup=True,
            use_jsonb=True,
            initialization_fail_on_error=False,
        )

    @staticmethod
    def _quick_note_text(note: QuickNote) -> str:
        parts = [f"速记内容：{note.content.strip()}"]
        tags = [tag.strip() for tag in list(note.topic_tags_json or []) if str(tag).strip()]
        if tags:
            parts.append(f"标签：{'、'.join(tags)}")
        source_text = (note.source_text or "").strip()
        if source_text:
            parts.append(f"原始描述：{source_text}")
        return "\n".join(parts)

    @staticmethod
    def _schedule_text(schedule: Schedule) -> str:
        parts = [f"标题：{schedule.title.strip()}"]
        if (schedule.location or "").strip():
            parts.append(f"地点：{schedule.location.strip()}")
        if (schedule.details or "").strip():
            parts.append(f"详情：{schedule.details.strip()}")
        if (schedule.source_text or "").strip():
            parts.append(f"原始描述：{schedule.source_text.strip()}")
        return "\n".join(parts)
