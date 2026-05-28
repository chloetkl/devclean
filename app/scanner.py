import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_database_session
from app.devin_client import DevinApiClient, get_devin_api_client
from app.models import CodeQualityAnalysis
from app.poller import schedule_session_polling, update_analysis_with_session

logger = logging.getLogger(__name__)

repository_analysis_router = APIRouter(tags=["Repository Analysis"])


class ManualScanRequest(BaseModel):
    repository_full_name: str


@repository_analysis_router.post("/api/repo/analyses", status_code=202)
async def trigger_manual_scan(
    scan_request: ManualScanRequest,
    background_tasks: BackgroundTasks,
    database_session: AsyncSession = Depends(get_database_session),
):
    if not scan_request.repository_full_name:
        raise HTTPException(status_code=400, detail="repository_full_name is required")

    analysis_record = CodeQualityAnalysis(
        repository_full_name=scan_request.repository_full_name,
        trigger_type="manual_trigger",
        analysis_status="pending",
    )
    database_session.add(analysis_record)
    await database_session.commit()
    await database_session.refresh(analysis_record)

    analysis_id = analysis_record.id

    background_tasks.add_task(
        _create_scan_session_and_start_polling,
        analysis_id,
        scan_request.repository_full_name,
    )

    return {
        "message": "Code quality scan initiated",
        "analysis_id": analysis_id,
    }


async def _create_scan_session_and_start_polling(
    analysis_id: int,
    repository_full_name: str,
) -> None:
    devin_client = get_devin_api_client()
    try:
        prompt = DevinApiClient.build_full_scan_prompt(repository_full_name)
        session_response = await devin_client.create_code_quality_session(
            prompt=prompt,
            repository_full_name=repository_full_name,
            session_tags=["devclean", "code-quality", "full-scan"],
            session_title=f"Code quality scan: {repository_full_name}",
        )

        session_id = session_response.get("session_id", "")
        session_url = devin_client.build_session_web_url(session_id)
        await update_analysis_with_session(analysis_id, session_id, session_url)
        await schedule_session_polling(analysis_id, session_id, devin_client)

    except Exception:
        logger.exception("Failed to create scan session for analysis %d", analysis_id)
        from app.poller import mark_analysis_error

        await mark_analysis_error(analysis_id, "Failed to create Devin scan session")
    finally:
        await devin_client.close()
