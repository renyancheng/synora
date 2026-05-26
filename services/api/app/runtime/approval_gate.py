from __future__ import annotations

from sqlalchemy.orm import Session

from app.domains.approval.service import consume_approval_request, create_approval_request


class ApprovalGate:
    def create(
        self,
        db: Session,
        *,
        user_id: int,
        action: str,
        draft_hash: str,
        payload: dict,
        normalized_payload: dict,
        evidence_digest: list[str],
        approval_scope: str | None = None,
    ):
        return create_approval_request(
            db,
            user_id=user_id,
            action=action,
            payload=payload,
            draft_hash=draft_hash,
            normalized_payload=normalized_payload,
            evidence_digest=evidence_digest,
            approval_scope=approval_scope,
        )

    def consume(self, db: Session, *, user_id: int, action: str, approval_token: str, draft_hash: str):
        return consume_approval_request(
            db,
            user_id=user_id,
            action=action,
            approval_token=approval_token,
            draft_hash=draft_hash,
        )
