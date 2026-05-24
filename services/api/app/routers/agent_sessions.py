from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.dependencies import get_current_user
from app.models import User
from app.runtime.errors import LLMServiceError
from app.runtime import get_runtime_executor
from app.runtime.model_adapter import ModelAdapter
from app.schemas.agent import AgentSessionIntakeRequest, AgentSessionIntakeResponse

router = APIRouter(prefix="/agent/sessions", tags=["agent_sessions"])


@router.post("/intake", response_model=AgentSessionIntakeResponse)
def intake(
    payload: AgentSessionIntakeRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AgentSessionIntakeResponse:
    executor = get_runtime_executor()
    payload_data = payload.model_dump(mode="json")
    workflow_payload = payload.model_dump(mode="json", exclude={"preferred_workflow"})
    try:
        workflow = ModelAdapter().route_workflow(payload_data)
        result = executor.execute_workflow(
            db,
            user_id=current_user.id,
            workflow=workflow,
            payload=workflow_payload,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except LLMServiceError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=exc.message) from exc
    return AgentSessionIntakeResponse(workflow=workflow, result=result)
