from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Attachment, AttachmentParseResult
from app.ocr_adapter import OCRAdapter
from app.storage import get_object_storage

TEXT_CONTENT_TYPES = {
    "text/plain",
    "text/csv",
    "application/json",
    "message/rfc822",
}


def _build_object_key(user_id: int, file_name: str) -> str:
    suffix = Path(file_name).suffix or ".bin"
    return f"user-{user_id}/{uuid4().hex}{suffix}"


async def upload_attachment(
    db: Session,
    *,
    user_id: int,
    source_type: str,
    upload: UploadFile,
) -> Attachment:
    settings = get_settings()
    payload = await upload.read()
    if not payload:
        raise ValueError("上传文件不能为空。")
    if len(payload) > settings.attachment_max_size_bytes:
        raise ValueError("上传文件超过大小限制，请压缩后重试。")

    file_name = upload.filename or f"attachment-{uuid4().hex}.bin"
    content_type = upload.content_type or "application/octet-stream"
    object_key = _build_object_key(user_id, file_name)
    storage = get_object_storage()
    storage.put_bytes(object_key, payload, content_type)

    attachment = Attachment(
        user_id=user_id,
        file_name=file_name,
        content_type=content_type,
        source_type=source_type,
        object_key=object_key,
        storage_bucket=storage.bucket,
        size_bytes=len(payload),
        status="uploaded",
    )
    db.add(attachment)
    db.commit()
    db.refresh(attachment)
    return attachment


def list_attachments_by_ids(db: Session, user_id: int, attachment_ids: list[int]) -> list[Attachment]:
    if not attachment_ids:
        return []
    rows = db.scalars(
        select(Attachment).where(Attachment.user_id == user_id, Attachment.id.in_(attachment_ids)).order_by(Attachment.id.asc())
    ).all()
    if len(rows) != len(set(attachment_ids)):
        raise ValueError("部分附件不存在或不属于当前用户。")
    return rows


def load_attachment_bytes(attachment: Attachment) -> bytes:
    return get_object_storage().get_bytes(attachment.object_key)


def ensure_parsed_attachment(db: Session, attachment: Attachment) -> AttachmentParseResult:
    existing = db.scalar(select(AttachmentParseResult).where(AttachmentParseResult.attachment_id == attachment.id))
    if existing and existing.status == "parsed":
        return existing

    payload = load_attachment_bytes(attachment)
    parser = OCRAdapter()
    lower_name = attachment.file_name.lower()
    is_image = attachment.content_type.startswith("image/") or lower_name.endswith((".png", ".jpg", ".jpeg", ".webp"))
    is_pdf = attachment.content_type == "application/pdf" or lower_name.endswith(".pdf")
    is_text = attachment.content_type in TEXT_CONTENT_TYPES or lower_name.endswith((".txt", ".json", ".csv", ".eml"))
    is_table = attachment.source_type in {"chat_record", "email"}

    if is_image or is_pdf:
        parsed = parser.parse_binary(
            file_name=attachment.file_name,
            content_type=attachment.content_type,
            payload=payload,
            is_table=is_table,
        )
        parser_kind = "ocr_space"
    elif is_text:
        parsed = parser.parse_text_like(
            file_name=attachment.file_name,
            content_type=attachment.content_type,
            payload=payload,
        )
        parser_kind = "text"
    else:
        raise ValueError("暂不支持该附件格式，请改为上传图片、PDF、TXT、JSON 或 EML。")

    if not existing:
        existing = AttachmentParseResult(
            attachment_id=attachment.id,
            parser_kind=parser_kind,
            raw_text=parsed["raw_text"],
            structured_json=parsed,
            status="parsed",
        )
        db.add(existing)
    else:
        existing.parser_kind = parser_kind
        existing.raw_text = parsed["raw_text"]
        existing.structured_json = parsed
        existing.status = "parsed"
        existing.error_message = None
        existing.updated_at = datetime.now(timezone.utc)
    attachment.status = "parsed"
    db.commit()
    db.refresh(existing)
    return existing


def build_attachment_summaries(db: Session, *, user_id: int, attachment_ids: list[int]) -> list[str]:
    summaries: list[str] = []
    attachments = list_attachments_by_ids(db, user_id, attachment_ids)
    for attachment in attachments:
        parsed = ensure_parsed_attachment(db, attachment)
        if parsed.raw_text.strip():
            summaries.append(parsed.raw_text.strip())
    return summaries
