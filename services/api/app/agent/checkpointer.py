"""LangGraph checkpointer 构建与生命周期管理。

Postgres 用 ``psycopg.AsyncConnection`` 直连 + ``AsyncPostgresSaver``（生产，
单 worker 长连接）；开发/测试用 ``AsyncSqliteSaver``（默认 ``sqlite``）。
checkpointer 为模块级单例，由 lifespan 或首轮 ``consume_stream`` 惰性创建，
``build_graph()`` 通过 ``get_checkpointer_sync()`` 在编译时绑定。
"""

from __future__ import annotations

import logging

import psycopg
from aiosqlite import connect
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from app.config import get_settings

logger = logging.getLogger(__name__)

_checkpointer: AsyncPostgresSaver | AsyncSqliteSaver | None = None


def _normalize_postgres_dsn(dsn: str) -> str:
    if dsn.startswith("postgresql+psycopg://"):
        return "postgresql://" + dsn[len("postgresql+psycopg://") :]
    return dsn


async def get_checkpointer() -> AsyncPostgresSaver | AsyncSqliteSaver:
    """惰性构建并 setup checkpointer，返回进程级单例。"""
    global _checkpointer
    if _checkpointer is None:
        settings = get_settings()
        if settings.langgraph_checkpoint_backend == "postgres":
            dsn = _normalize_postgres_dsn(settings.langgraph_checkpoint_db_url or settings.database_url)
            # autocommit=True：setup() 的 CREATE INDEX CONCURRENTLY 不能在事务块内执行
            conn = await psycopg.AsyncConnection.connect(dsn, autocommit=True)
            _checkpointer = AsyncPostgresSaver(conn)
        else:
            conn = await connect(settings.langgraph_checkpoint_sqlite_path)
            _checkpointer = AsyncSqliteSaver(conn)
        await _checkpointer.setup()
        logger.info(
            "langgraph_checkpointer_ready backend=%s",
            settings.langgraph_checkpoint_backend,
        )
    return _checkpointer


def get_checkpointer_sync() -> AsyncPostgresSaver | AsyncSqliteSaver | None:
    """供 build_graph 编译时读取已构建的 checkpointer（可能为 None）。"""
    return _checkpointer


async def setup_checkpointer() -> None:
    """确保 checkpointer 已构建并建表（幂等）。lifespan 与首轮消费调用。"""
    await get_checkpointer()


def reset_checkpointer() -> None:
    """清空单例（仅测试用）。"""
    global _checkpointer
    _checkpointer = None


async def delete_checkpoint(thread_id: str) -> None:
    """删除指定 thread 的 checkpoint（rewind / delete_conversation 清理孤儿）。"""
    if not thread_id:
        return
    checkpointer = get_checkpointer_sync()
    if checkpointer is None:
        return
    try:
        await checkpointer.adelete_thread(thread_id)
    except Exception:
        logger.exception("delete_checkpoint_failed thread_id=%s", thread_id)
