import asyncio
import json
import logging
from datetime import datetime, timezone

from app.devin_client import DevinApiClient
from app.models import CodeQualityAnalysis

logger = logging.getLogger(__name__)

TERMINAL_SESSION_STATUSES = {"exit", "error", "suspended"}
SETTLED_STATUS_DETAILS = {"finished", "waiting_for_user"}


async def schedule_session_polling(
    analysis_id: int,
    session_id: str,
    devin_client: DevinApiClient,
) -> None:
    from app.config import application_settings

    poll_interval = application_settings.session_poll_interval_seconds
    poll_timeout = application_settings.session_poll_timeout_seconds
    elapsed_seconds = 0

    try:
        while elapsed_seconds < poll_timeout:
            await asyncio.sleep(poll_interval)
            elapsed_seconds += poll_interval

            try:
                session_details = await devin_client.get_session_details(session_id)
            except Exception:
                logger.exception("Failed to poll session %s", session_id)
                continue

            session_status = session_details.get("status", "")
            session_status_detail = session_details.get("status_detail", "")

            is_terminal = session_status in TERMINAL_SESSION_STATUSES
            is_settled_running = (
                session_status == "running" and session_status_detail in SETTLED_STATUS_DETAILS
            )
            # If Devin has already created a fix PR, treat the session as done
            # immediately — don't wait for CI checks or session termination.
            has_fix_pr = bool(
                (session_details.get("structured_output") or {}).get("fix_pr_url")
                or (session_details.get("pull_requests") or [])
            )

            if is_terminal or is_settled_running or has_fix_pr:
                await _process_completed_session(analysis_id, session_details)
                return

        await mark_analysis_error(analysis_id, "Session polling timed out")
        logger.warning("Analysis %d timed out", analysis_id)

    except Exception:
        logger.exception("Polling error for analysis %d, session %s", analysis_id, session_id)
        await mark_analysis_error(analysis_id, "Unexpected polling error")


async def _process_completed_session(analysis_id: int, session_details: dict) -> None:
    from app.database import async_database_session_factory

    session_status = session_details.get("status", "")
    structured_output = session_details.get("structured_output")

    async with async_database_session_factory() as database_session:
        analysis_record = await database_session.get(CodeQualityAnalysis, analysis_id)
        if not analysis_record:
            logger.warning("Analysis record %d not found", analysis_id)
            return

        now = datetime.now(timezone.utc)
        analysis_record.completed_at = now
        delta = now - analysis_record.initiated_at
        analysis_record.duration_seconds = int(delta.total_seconds())

        if session_status == "error":
            analysis_record.analysis_status = "error"
            analysis_record.error_message = "Devin session ended with error"
            await database_session.commit()
            return

        if not structured_output:
            analysis_record.analysis_status = "error"
            analysis_record.error_message = "No structured output returned from Devin session"
            await database_session.commit()
            return

        issues_detected = structured_output.get("issues_found", False)
        issues_list = structured_output.get("issues", [])
        summary = structured_output.get("summary", "")

        # Prefer fix_pr_url from structured output; fall back to the top-level
        # pull_requests array that the v3 API returns on the session object.
        fix_pr_url = structured_output.get("fix_pr_url")
        if not fix_pr_url:
            pull_requests = session_details.get("pull_requests") or []
            if pull_requests:
                fix_pr_url = pull_requests[0].get("pr_url")

        if issues_list:
            analysis_record.issues_found = json.dumps(issues_list)
            analysis_record.issue_count = len(issues_list)
        else:
            analysis_record.issue_count = 0

        analysis_record.fix_pr_url = fix_pr_url

        if not issues_detected:
            analysis_record.analysis_status = "no_issues_found"
        elif fix_pr_url:
            analysis_record.analysis_status = "fix_pr_created"
        else:
            analysis_record.analysis_status = "error"
            analysis_record.error_message = summary or "Issues found but no fix PR was created"

        await database_session.commit()
        logger.info(
            "Analysis %d completed: status=%s, issues=%s",
            analysis_id,
            analysis_record.analysis_status,
            issues_detected,
        )


async def mark_analysis_error(analysis_id: int, error_message: str) -> None:
    from app.database import async_database_session_factory

    async with async_database_session_factory() as database_session:
        analysis_record = await database_session.get(CodeQualityAnalysis, analysis_id)
        if analysis_record:
            analysis_record.analysis_status = "error"
            analysis_record.error_message = error_message
            now = datetime.now(timezone.utc)
            analysis_record.completed_at = now
            delta = now - analysis_record.initiated_at
            analysis_record.duration_seconds = int(delta.total_seconds())
            await database_session.commit()


async def update_analysis_with_session(
    analysis_id: int, session_id: str, session_url: str
) -> None:
    from app.database import async_database_session_factory

    async with async_database_session_factory() as database_session:
        analysis_record = await database_session.get(CodeQualityAnalysis, analysis_id)
        if analysis_record:
            analysis_record.devin_session_id = session_id
            analysis_record.devin_session_url = session_url
            analysis_record.analysis_status = "analyzing"
            await database_session.commit()
