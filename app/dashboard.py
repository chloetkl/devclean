import json
import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import application_settings
from app.database import get_database_session
from app.devin_client import DevinApiClient, get_devin_api_client
from app.models import CodeQualityAnalysis
from app.poller import (
    mark_analysis_error,
    schedule_session_polling,
    update_analysis_with_session,
)

logger = logging.getLogger(__name__)

dashboard_router = APIRouter(tags=["Dashboard"])

templates = Jinja2Templates(directory="templates")

ISSUE_CATEGORIES = (
    "DOC_DRIFT",
    "COMPLEX_DEAD_CODE",
    "INCONSISTENT_PATTERNS",
    "INCOMPLETE_ERROR_HANDLING",
)


def _check_configuration() -> dict[str, bool]:
    return {
        "devin_api_token": bool(application_settings.devin_api_token),
        "devin_organization_id": bool(application_settings.devin_organization_id),
        "github_webhook_secret": bool(application_settings.github_webhook_secret),
    }


@dashboard_router.get("/")
async def root(request: Request):
    config = _check_configuration()
    if all(config.values()):
        return RedirectResponse(url="/dashboard")
    return templates.TemplateResponse(
        request=request,
        name="setup.html",
        context={
            "config": config,
            "webhook_url": str(request.base_url).rstrip("/") + "/api/github/events",
        },
    )


@dashboard_router.get("/setup")
async def render_setup_page(request: Request):
    config = _check_configuration()
    return templates.TemplateResponse(
        request=request,
        name="setup.html",
        context={
            "config": config,
            "webhook_url": str(request.base_url).rstrip("/") + "/api/github/events",
        },
    )


@dashboard_router.get("/dashboard")
async def render_dashboard_page(
    request: Request,
    database_session: AsyncSession = Depends(get_database_session),
):
    statistics = await _compute_dashboard_statistics(database_session)
    recent_analyses = await _get_recent_analyses(database_session, limit=50)
    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "statistics": statistics,
            "analyses": recent_analyses,
        },
    )


@dashboard_router.get("/api/statistics")
async def get_dashboard_statistics(
    database_session: AsyncSession = Depends(get_database_session),
):
    return await _compute_dashboard_statistics(database_session)


@dashboard_router.get("/api/analyses")
async def list_all_analyses(
    database_session: AsyncSession = Depends(get_database_session),
    limit: int = 50,
    offset: int = 0,
):
    query = (
        select(CodeQualityAnalysis)
        .order_by(CodeQualityAnalysis.initiated_at.desc())
        .limit(limit)
        .offset(offset)
    )
    result = await database_session.execute(query)
    analyses = result.scalars().all()
    return [_serialize_analysis_record(a) for a in analyses]


@dashboard_router.get("/api/analyses/{analysis_id}")
async def get_analysis_details(
    analysis_id: int,
    database_session: AsyncSession = Depends(get_database_session),
):
    analysis_record = await database_session.get(CodeQualityAnalysis, analysis_id)
    if not analysis_record:
        raise HTTPException(status_code=404, detail="Analysis not found")
    return _serialize_analysis_record(analysis_record)


@dashboard_router.post("/api/analyses/{analysis_id}/retry", status_code=202)
async def retry_failed_analysis(
    analysis_id: int,
    background_tasks: BackgroundTasks,
    database_session: AsyncSession = Depends(get_database_session),
):
    analysis_record = await database_session.get(CodeQualityAnalysis, analysis_id)
    if not analysis_record:
        raise HTTPException(status_code=404, detail="Analysis not found")
    if analysis_record.analysis_status != "error":
        raise HTTPException(status_code=400, detail="Only failed analyses can be retried")

    max_retries = 3
    if analysis_record.retry_count >= max_retries:
        raise HTTPException(
            status_code=400,
            detail=f"Maximum retry limit ({max_retries}) reached",
        )

    analysis_record.retry_count += 1
    analysis_record.analysis_status = "pending"
    analysis_record.error_message = None
    analysis_record.devin_session_id = None
    analysis_record.devin_session_url = None
    analysis_record.fix_pr_url = None
    analysis_record.issues_found = None
    analysis_record.issue_count = None
    analysis_record.completed_at = None
    analysis_record.duration_seconds = None
    await database_session.commit()

    repo = analysis_record.repository_full_name
    trigger = analysis_record.trigger_type

    if trigger == "pull_request_webhook":
        background_tasks.add_task(
            _retry_pr_analysis,
            analysis_id,
            repo,
            analysis_record.source_pr_number or 0,
            analysis_record.source_pr_title or "",
            analysis_record.source_pr_url or "",
        )
    else:
        background_tasks.add_task(
            _retry_scan_analysis,
            analysis_id,
            repo,
            analysis_record.scan_path,
        )

    return {"message": "Analysis queued for retry", "analysis_id": analysis_id}


async def _retry_pr_analysis(
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
            session_title=f"Code quality retry: {repository_full_name}#{pr_number}",
        )
        session_id = session_response.get("session_id", "")
        session_url = devin_client.build_session_web_url(session_id)
        await update_analysis_with_session(analysis_id, session_id, session_url)
        await schedule_session_polling(analysis_id, session_id, devin_client)
    except Exception:
        logger.exception("Failed to retry PR analysis %d", analysis_id)
        await mark_analysis_error(analysis_id, "Failed to create Devin session on retry")
    finally:
        await devin_client.close()


async def _retry_scan_analysis(
    analysis_id: int,
    repository_full_name: str,
    scan_path: str | None = None,
) -> None:
    devin_client = get_devin_api_client()
    try:
        prompt = DevinApiClient.build_scan_prompt(
            repository_full_name, scan_path=scan_path
        )
        session_response = await devin_client.create_code_quality_session(
            prompt=prompt,
            repository_full_name=repository_full_name,
            session_tags=["devclean", "code-quality", "full-scan"],
            session_title=f"Code quality scan retry: {repository_full_name}",
        )
        session_id = session_response.get("session_id", "")
        session_url = devin_client.build_session_web_url(session_id)
        await update_analysis_with_session(analysis_id, session_id, session_url)
        await schedule_session_polling(analysis_id, session_id, devin_client)
    except Exception:
        logger.exception("Failed to retry scan analysis %d", analysis_id)
        await mark_analysis_error(analysis_id, "Failed to create Devin scan session on retry")
    finally:
        await devin_client.close()


async def _compute_dashboard_statistics(database_session: AsyncSession) -> dict:
    total_count = await _count_analyses_by_statuses(database_session, None)
    no_issues_count = await _count_analyses_by_statuses(
        database_session, ("no_issues_found",)
    )
    fix_pr_created_count = await _count_analyses_by_statuses(
        database_session, ("fix_pr_created",)
    )
    in_progress_count = await _count_analyses_by_statuses(
        database_session, ("pending", "analyzing")
    )
    error_count = await _count_analyses_by_statuses(database_session, ("error",))

    error_rate = (error_count / total_count * 100) if total_count > 0 else 0.0
    avg_duration = await _compute_average_duration_seconds(database_session)

    issues_by_category = await _count_issues_by_category(database_session)
    total_issues = sum(issues_by_category.values())

    return {
        "total_analyses": total_count,
        "no_issues_found_count": no_issues_count,
        "fix_pr_created_count": fix_pr_created_count,
        "in_progress_count": in_progress_count,
        "error_count": error_count,
        "error_rate_percentage": round(error_rate, 1),
        "average_duration_seconds": round(avg_duration, 1),
        "issues_by_category": issues_by_category,
        "total_issues_detected": total_issues,
        "total_fix_prs_created": fix_pr_created_count,
    }


async def _count_analyses_by_statuses(
    database_session: AsyncSession, statuses: tuple[str, ...] | None
) -> int:
    query = select(func.count(CodeQualityAnalysis.id))
    if statuses is not None:
        query = query.where(CodeQualityAnalysis.analysis_status.in_(statuses))
    result = await database_session.execute(query)
    return result.scalar() or 0


async def _compute_average_duration_seconds(database_session: AsyncSession) -> float:
    query = select(func.avg(CodeQualityAnalysis.duration_seconds)).where(
        CodeQualityAnalysis.duration_seconds.isnot(None)
    )
    result = await database_session.execute(query)
    avg_val = result.scalar()
    return float(avg_val) if avg_val is not None else 0.0


async def _count_issues_by_category(database_session: AsyncSession) -> dict[str, int]:
    counts: dict[str, int] = {cat: 0 for cat in ISSUE_CATEGORIES}

    query = select(CodeQualityAnalysis.issues_found).where(
        CodeQualityAnalysis.issues_found.isnot(None)
    )
    result = await database_session.execute(query)
    rows = result.scalars().all()

    for issues_json in rows:
        try:
            issues = json.loads(issues_json)
        except (json.JSONDecodeError, TypeError):
            continue
        for issue in issues:
            category = issue.get("category", "")
            if category in counts:
                counts[category] += 1

    return counts


async def _get_recent_analyses(
    database_session: AsyncSession, limit: int = 50
) -> list[dict]:
    query = (
        select(CodeQualityAnalysis)
        .order_by(CodeQualityAnalysis.initiated_at.desc())
        .limit(limit)
    )
    result = await database_session.execute(query)
    analyses = result.scalars().all()
    return [_serialize_analysis_record(a) for a in analyses]


def _serialize_analysis_record(analysis: CodeQualityAnalysis) -> dict:
    issues_found = None
    if analysis.issues_found:
        try:
            issues_found = json.loads(analysis.issues_found)
        except json.JSONDecodeError:
            issues_found = None

    return {
        "id": analysis.id,
        "trigger_type": analysis.trigger_type,
        "repository_full_name": analysis.repository_full_name,
        "scan_path": analysis.scan_path,
        "source_pr_number": analysis.source_pr_number,
        "source_pr_title": analysis.source_pr_title,
        "source_pr_url": analysis.source_pr_url,
        "devin_session_id": analysis.devin_session_id,
        "devin_session_url": analysis.devin_session_url,
        "fix_pr_url": analysis.fix_pr_url,
        "analysis_status": analysis.analysis_status,
        "issues_found": issues_found,
        "issue_count": analysis.issue_count,
        "error_message": analysis.error_message,
        "retry_count": analysis.retry_count,
        "initiated_at": analysis.initiated_at.isoformat() if analysis.initiated_at else None,
        "completed_at": analysis.completed_at.isoformat() if analysis.completed_at else None,
        "duration_seconds": analysis.duration_seconds,
    }
