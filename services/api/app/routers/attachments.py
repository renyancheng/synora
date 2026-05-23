from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.dependencies import get_current_user
from app.domains.attachment.service import upload_attachment
from app.models import User
from app.schemas.attachment import AttachmentUploadResponse

router = APIRouter(prefix="/attachments", tags=["attachments"])


@router.post("/upload", response_model=AttachmentUploadResponse)
async def upload_attachment_endpoint(
    source_type: str = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AttachmentUploadResponse:
    try:
        attachment = await upload_attachment(
            db,
            user_id=current_user.id,
            source_type=source_type,
            upload=file,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return AttachmentUploadResponse(
        attachment_id=attachment.id,
        file_name=attachment.file_name,
        content_type=attachment.content_type,
        size_bytes=attachment.size_bytes,
        source_type=attachment.source_type,
        created_at=attachment.created_at,
    )
