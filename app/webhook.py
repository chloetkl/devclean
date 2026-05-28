import hashlib
import hmac
import json
import logging

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import application_settings
from app.database import get_database_session
from app.devin_client import DevinApiClient, get_devin_api_client
from app.models import CodeQualityAnalysis
from app.poller import schedule_session_polling, update_analysis_with_session

logger = logging.getLogger(__name__)

github_events_router = APIRouter(tags=["GitHub Events"])


def verify_github_webhook_signature(
    payload_body: bytes, signature_header: str, secret: str
) -> bool:
    if not signature_header:
        return False
    expected_signature = "sha256=" + hmac.new(
        secret.encode("utf-8"),
        payload_body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected_signature, signature_header)


@github_events_router.post("/api/github/events", status_code=202)
async def handle_github_webhook_event(
    request: Request,
    background_tasks: BackgroundTasks,
    database_session: AsyncSession = Depends(get_database_session),
    x_github_event: str | None = Header(None),
    x_hub_signature_256: str | None = Header(None),
):
    raw_payload_body = await request.body()

    webhook_secret = application_settings.github_webhook_secret
    if webhook_secret:
        if not verify_github_webhook_signature(
            raw_payload_body, x_hub_signature_256 or "", webhook_secret
        ):
            raise HTTPException(status_code=401, detail="Invalid webhook signature")

    if x_github_event != "pull_request":
        return {"message": f"Ignoring event type: {x_github_event}"}

    event_payload = json.loads(raw_payload_body)
    webhook_action = event_payload.get("action")

    if webhook_action not in ("opened", "reopened"):
        return {"message": f"Ignoring pull_request action: {webhook_action}"}

    pull_request_data = event_payload.get("pull_request", {})
    repository_data = event_payload.get("repository", {})

    pr_author = pull_request_data.get("user", {}).get("login", "")
    if pr_author == "devin-ai-integration[bot]":
        return {"message": "Ignoring PR opened by Devin bot"}

    repository_full_name = repository_data.get("full_name", "")
    pr_number = pull_request_data.get("number")
    pr_title = pull_request_data.get("title", "")
    pr_url = pull_request_data.get("html_url", "")

    if not repository_full_name or not pr_number:
        raise HTTPException(status_code=400, detail="Missing repository or PR information")

    analysis_record = CodeQualityAnalysis(
        repository_full_name=repository_full_name,
        source_pr_number=pr_number,
        source_pr_title=pr_title,
        source_pr_url=pr_url,
        trigger_type="pull_request_webhook",
        analysis_status="pending",
    )
    database_session.add(analysis_record)
    await database_session.commit()
    await database_session.refresh(analysis_record)

    analysis_id = analysis_record.id

    background_tasks.add_task(
        _create_devin_session_and_start_polling,
        analysis_id,
        repository_full_name,
        pr_number,
        pr_title,
        pr_url,
    )

    return {
        "message": "Code quality analysis initiated",
        "analysis_id": analysis_id,
    }


async def _create_devin_session_and_start_polling(
    analysis_id: int,
    repository_full_name: str,
    pr_number: int,
    pr_title: str,
    pr_url: str,
) -> None:
    devin_client = get_devin_api_client()
    try:
        prompt = DevinApiClient.build_pr_analysis_prompt(
            repository_full_name, pr_number, pr_title, pr_url
        )

        session_response = await devin_client.create_code_quality_session(
            prompt=prompt,
            repository_full_name=repository_full_name,
            session_title=f"Code quality check: {repository_full_name}#{pr_number}",
        )

        session_id = session_response.get("session_id", "")
        session_url = devin_client.build_session_web_url(session_id)
        await update_analysis_with_session(analysis_id, session_id, session_url)
        await schedule_session_polling(analysis_id, session_id, devin_client)

    except Exception:
        logger.exception("Failed to create Devin session for analysis %d", analysis_id)
        from app.poller import mark_analysis_error

        await mark_analysis_error(analysis_id, "Failed to create Devin session")
    finally:
        await devin_client.close()
