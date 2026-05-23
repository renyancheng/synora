from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Attachment, AttachmentParseResult
from app.storage import get_object_storage

SUPPORTED_TEXT_TYPES = {
    "text/plain",
    "text/csv",
    "application/json",
    "text/markdown",
}

SUPPORTED_TEXT_SUFFIXES = {".txt", ".csv", ".json", ".md"}
SUPPORTED_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}


@dataclass
class AttachmentPromptAsset:
    attachment_id: int
    file_name: str
    kind: str
    parts: list[dict]
    raw_text: str = ""
    meta: dict | None = None


def _build_object_key(user_id: int, file_name: str) -> str:
    suffix = Path(file_name).suffix or ".bin"
    return f"user-{user_id}/{uuid4().hex}{suffix}"


def _reject_unsupported_mail_file(upload: UploadFile) -> None:
    file_name = (upload.filename or "").lower()
    content_type = (upload.content_type or "").lower()
    if file_name.endswith(".eml") or content_type == "message/rfc822":
        raise ValueError("可直接粘贴邮件正文，不支持导入邮件文件。")


def _decode_text(payload: bytes) -> str:
    for encoding in ("utf-8", "utf-8-sig", "gb18030", "gbk"):
        try:
            return payload.decode(encoding)
        except UnicodeDecodeError:
            continue
    return payload.decode("utf-8", errors="ignore")


def _make_data_url(content_type: str, payload: bytes) -> str:
    return f"data:{content_type};base64,{base64.b64encode(payload).decode('ascii')}"


def _render_pdf_pages(payload: bytes, file_name: str) -> list[tuple[str, bytes]]:
    try:
        import fitz
    except ImportError as exc:
        raise ValueError("当前环境未安装 PDF 渲染依赖，请改为上传截图或文本。") from exc

    settings = get_settings()
    pages: list[tuple[str, bytes]] = []
    with fitz.open(stream=payload, filetype="pdf") as document:
        page_count = min(document.page_count, settings.llm_max_pdf_pages)
        for index in range(page_count):
            page = document.load_page(index)
            pixmap = page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False)
            image_bytes = pixmap.tobytes("png")
            pages.append((f"{file_name} 第 {index + 1} 页", image_bytes))
    return pages


async def upload_attachment(
    db: Session,
    *,
    user_id: int,
    upload: UploadFile,
) -> Attachment:
    settings = get_settings()
    _reject_unsupported_mail_file(upload)

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
        source_type="attachment",
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
        select(Attachment)
        .where(Attachment.user_id == user_id, Attachment.id.in_(attachment_ids))
        .order_by(Attachment.id.asc())
    ).all()
    if len(rows) != len(set(attachment_ids)):
        raise ValueError("部分附件不存在或不属于当前用户。")
    return rows


def load_attachment_bytes(attachment: Attachment) -> bytes:
    return get_object_storage().get_bytes(attachment.object_key)


def _upsert_parse_result(
    db: Session,
    attachment: Attachment,
    *,
    raw_text: str,
    meta: dict,
) -> AttachmentParseResult:
    existing = db.scalar(select(AttachmentParseResult).where(AttachmentParseResult.attachment_id == attachment.id))
    if not existing:
        existing = AttachmentParseResult(
            attachment_id=attachment.id,
            parser_kind="qwen_multimodal",
            raw_text=raw_text,
            structured_json=meta,
            status="parsed",
        )
        db.add(existing)
    else:
        existing.parser_kind = "qwen_multimodal"
        existing.raw_text = raw_text
        existing.structured_json = meta
        existing.status = "parsed"
        existing.error_message = None
        existing.updated_at = datetime.now(timezone.utc)
    attachment.status = "parsed"
    db.commit()
    db.refresh(existing)
    return existing


def build_attachment_prompt_assets(db: Session, *, user_id: int, attachment_ids: list[int]) -> list[AttachmentPromptAsset]:
    attachments = list_attachments_by_ids(db, user_id, attachment_ids)
    assets: list[AttachmentPromptAsset] = []

    for attachment in attachments:
        payload = load_attachment_bytes(attachment)
        lower_name = attachment.file_name.lower()
        content_type = attachment.content_type.lower()

        if lower_name.endswith(".eml") or content_type == "message/rfc822":
            raise ValueError("可直接粘贴邮件正文，不支持导入邮件文件。")

        if content_type.startswith("image/") or any(lower_name.endswith(suffix) for suffix in SUPPORTED_IMAGE_SUFFIXES):
            parts = [
                {"type": "text", "text": f"附件《{attachment.file_name}》"},
                {"type": "image_url", "image_url": {"url": _make_data_url(content_type or "image/png", payload)}},
            ]
            meta = {"kind": "image", "file_name": attachment.file_name, "content_type": attachment.content_type}
            _upsert_parse_result(db, attachment, raw_text="", meta=meta)
            assets.append(
                AttachmentPromptAsset(
                    attachment_id=attachment.id,
                    file_name=attachment.file_name,
                    kind="image",
                    parts=parts,
                    meta=meta,
                )
            )
            continue

        if content_type == "application/pdf" or lower_name.endswith(".pdf"):
            rendered_pages = _render_pdf_pages(payload, attachment.file_name)
            if not rendered_pages:
                raise ValueError("PDF 未能成功处理，请改为上传截图或直接粘贴文本。")
            parts = [{"type": "text", "text": f"附件《{attachment.file_name}》共 {len(rendered_pages)} 页。"}]
            for page_label, image_bytes in rendered_pages:
                parts.append({"type": "text", "text": page_label})
                parts.append({"type": "image_url", "image_url": {"url": _make_data_url("image/png", image_bytes)}})
            meta = {"kind": "pdf", "file_name": attachment.file_name, "page_count": len(rendered_pages)}
            _upsert_parse_result(db, attachment, raw_text="", meta=meta)
            assets.append(
                AttachmentPromptAsset(
                    attachment_id=attachment.id,
                    file_name=attachment.file_name,
                    kind="pdf",
                    parts=parts,
                    meta=meta,
                )
            )
            continue

        if content_type in SUPPORTED_TEXT_TYPES or Path(lower_name).suffix in SUPPORTED_TEXT_SUFFIXES:
            text = _decode_text(payload).strip()
            meta = {"kind": "text", "file_name": attachment.file_name, "content_type": attachment.content_type}
            _upsert_parse_result(db, attachment, raw_text=text, meta=meta)
            assets.append(
                AttachmentPromptAsset(
                    attachment_id=attachment.id,
                    file_name=attachment.file_name,
                    kind="text",
                    raw_text=text,
                    parts=[{"type": "text", "text": f"附件《{attachment.file_name}》内容：\n{text}"}],
                    meta=meta,
                )
            )
            continue

        raise ValueError("暂不支持该附件格式，请上传图片、PDF 或常见文本文件。")

    return assets


def build_attachment_texts(db: Session, *, user_id: int, attachment_ids: list[int]) -> list[str]:
    texts: list[str] = []
    for asset in build_attachment_prompt_assets(db, user_id=user_id, attachment_ids=attachment_ids):
        if asset.raw_text.strip():
            texts.append(asset.raw_text.strip())
        else:
            texts.append(f"已附加文件《{asset.file_name}》。")
    return texts
