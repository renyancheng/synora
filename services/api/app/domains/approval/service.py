from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import ApprovalRequest
from app.security import future_utc, mint_token, sha256_text


def create_approval_request(
    db: Session,
    *,
    user_id: int,
    action: str,
    payload: dict,
    draft_hash: str,
    normalized_payload: dict,
    evidence_digest: list[str],
    approval_scope: str | None = None,
) -> tuple[ApprovalRequest, str]:
    if approval_scope:
        stale_items = db.scalars(
            select(ApprovalRequest).where(
                ApprovalRequest.user_id == user_id,
                ApprovalRequest.action == action,
                ApprovalRequest.approval_scope == approval_scope,
                ApprovalRequest.status == "pending",
            )
        ).all()
        for item in stale_items:
            item.status = "superseded"
        if stale_items:
            db.commit()

    token = mint_token()
    approval = ApprovalRequest(
        user_id=user_id,
        action=action,
        draft_hash=draft_hash,
        payload_json=json.dumps(payload, ensure_ascii=False, sort_keys=True),
        normalized_payload_json=json.dumps(normalized_payload, ensure_ascii=False, sort_keys=True),
        evidence_digest_json=json.dumps(evidence_digest, ensure_ascii=False),
        approval_scope=approval_scope,
        token_hash=sha256_text(token),
        expires_at=future_utc(get_settings().approval_ttl_hours),
    )
    db.add(approval)
    db.commit()
    db.refresh(approval)
    return approval, token


def consume_approval_request(db: Session, *, user_id: int, action: str, approval_token: str, draft_hash: str) -> ApprovalRequest:
    approval = db.scalar(
        select(ApprovalRequest).where(
            ApprovalRequest.user_id == user_id,
            ApprovalRequest.action == action,
            ApprovalRequest.token_hash == sha256_text(approval_token),
        )
    )
    if not approval:
        raise ValueError("审批令牌无效。")
    if approval.status == "superseded":
        raise ValueError("该确认已失效，请使用最新卡片或最新预检结果重新确认。")
    if approval.status != "pending":
        raise ValueError("审批令牌无效。")
    expires_at = approval.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at <= datetime.now(timezone.utc):
        approval.status = "expired"
        db.commit()
        raise ValueError("审批令牌已过期，请重新生成。")
    if approval.draft_hash != draft_hash:
        raise ValueError("审批草稿校验失败，请重新确认。")

    approval.status = "confirmed"
    approval.confirmed_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(approval)
    return approval
