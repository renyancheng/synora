from __future__ import annotations

import re
from codecs import BOM_UTF8
from email import policy
from email.header import decode_header, make_header
from email.parser import BytesParser
from html import unescape
from io import BytesIO

import httpx
from pypdf import PdfReader

from app.config import Settings, get_settings


def _strip_html_tags(value: str) -> str:
    text = re.sub(r"<[^>]+>", " ", value)
    text = unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _decode_header_value(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value))).strip()
    except Exception:
        return str(value).strip()


class OCRAdapter:
    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    def _validate_size(self, payload: bytes) -> None:
        if len(payload) > self._settings.ocr_max_file_size_bytes:
            raise ValueError("文件超过 OCR.space 免费版大小限制，请压缩后重试或改为粘贴文本。")

    def _validate_pdf_pages(self, payload: bytes) -> None:
        reader = PdfReader(BytesIO(payload))
        if len(reader.pages) > self._settings.ocr_max_pdf_pages:
            raise ValueError("PDF 页数超过 OCR.space 免费版限制，请拆分后重试或改为粘贴文本。")

    def parse_binary(self, *, file_name: str, content_type: str, payload: bytes, is_table: bool = False) -> dict:
        self._validate_size(payload)
        lower_name = file_name.lower()
        if content_type == "application/pdf" or lower_name.endswith(".pdf"):
            self._validate_pdf_pages(payload)

        with httpx.Client(timeout=self._settings.deepseek_timeout_seconds) as client:
            response = client.post(
                self._settings.ocr_space_base_url,
                data={
                    "apikey": self._settings.ocr_space_api_key,
                    "language": self._settings.ocr_space_language,
                    "isOverlayRequired": "false",
                    "detectOrientation": "true",
                    "scale": "true",
                    "OCREngine": str(self._settings.ocr_space_engine),
                    "isTable": "true" if is_table else "false",
                },
                files={"file": (file_name, payload, content_type)},
            )
            response.raise_for_status()
            result = response.json()

        if result.get("IsErroredOnProcessing"):
            detail = result.get("ErrorMessage") or result.get("ErrorDetails") or "OCR 解析失败"
            if isinstance(detail, list):
                detail = "; ".join(str(item) for item in detail)
            raise ValueError(f"OCR.space 解析失败：{detail}")

        pages: list[dict] = []
        blocks: list[dict] = []
        raw_text_parts: list[str] = []
        for index, item in enumerate(result.get("ParsedResults", []), start=1):
            page_text = (item.get("ParsedText") or "").strip()
            if page_text:
                raw_text_parts.append(page_text)
            pages.append({"page": index, "text": page_text})
            overlay = item.get("TextOverlay") or {}
            for line in overlay.get("Lines", []):
                line_text = " ".join(word.get("WordText", "") for word in line.get("Words", []))
                line_text = re.sub(r"\s+", " ", line_text).strip()
                if line_text:
                    blocks.append({"page": index, "text": line_text})

        return {
            "raw_text": "\n".join(part for part in raw_text_parts if part).strip(),
            "pages": pages,
            "blocks": blocks,
            "ocr_provider_meta": {
                "provider": "ocr.space",
                "parsed_results": len(pages),
            },
        }

    def parse_text_like(self, *, file_name: str, content_type: str, payload: bytes) -> dict:
        lower_name = file_name.lower()
        if lower_name.endswith(".eml") or content_type == "message/rfc822":
            return self.parse_eml(file_name=file_name, payload=payload)
        decoded = payload.decode("utf-8-sig", errors="ignore")
        return {
            "raw_text": decoded.strip(),
            "pages": [{"page": 1, "text": decoded.strip()}],
            "blocks": [{"page": 1, "text": line.strip()} for line in decoded.splitlines() if line.strip()],
            "ocr_provider_meta": {"provider": "builtin"},
        }

    def parse_eml(self, *, file_name: str, payload: bytes) -> dict:
        if payload.startswith(BOM_UTF8):
            payload = payload[len(BOM_UTF8) :]
        message = BytesParser(policy=policy.default).parsebytes(payload)
        attachments: list[dict] = []
        body_parts: list[str] = []
        for part in message.walk():
            if part.is_multipart():
                continue
            disposition = part.get_content_disposition()
            content_type = part.get_content_type()
            file_name_part = part.get_filename()
            if disposition == "attachment" and file_name_part:
                extracted = part.get_payload(decode=True) or b""
                attachment_meta = {
                    "file_name": file_name_part,
                    "content_type": content_type,
                    "size_bytes": len(extracted),
                }
                if content_type.startswith("image/") or content_type == "application/pdf" or file_name_part.lower().endswith(".pdf"):
                    try:
                        ocr_result = self.parse_binary(
                            file_name=file_name_part,
                            content_type=content_type,
                            payload=extracted,
                            is_table=False,
                        )
                        attachment_meta["raw_text"] = ocr_result["raw_text"]
                        if ocr_result["raw_text"]:
                            body_parts.append(ocr_result["raw_text"])
                    except Exception as exc:
                        attachment_meta["error"] = str(exc)
                elif content_type.startswith("text/"):
                    charset = part.get_content_charset() or "utf-8"
                    text_part = extracted.decode(charset, errors="ignore").lstrip("\ufeff").strip()
                    attachment_meta["raw_text"] = text_part
                    if text_part:
                        body_parts.append(text_part)
                attachments.append(attachment_meta)
                continue

            if content_type == "text/plain":
                value = part.get_content()
                if value:
                    body_parts.append(str(value).lstrip("\ufeff").strip())
            elif content_type == "text/html":
                value = part.get_content()
                if value:
                    body_parts.append(_strip_html_tags(str(value)))

        subject = _decode_header_value(message.get("subject"))
        sender = _decode_header_value(message.get("from"))
        sent_at = _decode_header_value(message.get("date"))
        prefix_lines = [
            line
            for line in [f"邮件主题：{subject}", f"发件人：{sender}", f"发送时间：{sent_at}"]
            if line.split("：", 1)[-1]
        ]
        merged = "\n".join(prefix_lines + [item for item in body_parts if item])
        return {
            "raw_text": merged.strip(),
            "pages": [{"page": 1, "text": merged.strip()}],
            "blocks": [{"page": 1, "text": line.strip()} for line in merged.splitlines() if line.strip()],
            "ocr_provider_meta": {"provider": "builtin_eml", "attachments": attachments, "file_name": file_name},
        }
