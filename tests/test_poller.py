"""
Tests for the session polling logic in app/poller.py.

Each test wires up an in-memory SQLite DB, inserts a CodeQualityAnalysis
record, mocks the Devin API client to return a controlled sequence of
session payloads, and then asserts that the DB record ends up in the
expected terminal state.
"""

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import CodeQualityAnalysis, DatabaseBaseModel
from app.poller import _process_completed_session, schedule_session_polling


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _session_payload(
    status: str,
    status_detail: str = "",
    pull_requests: list | None = None,
    structured_output: dict | None = None,
) -> dict:
    return {
        "session_id": "devin-test123",
        "status": status,
        "status_detail": status_detail,
        "pull_requests": pull_requests or [],
        "structured_output": structured_output,
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
async def db_engine():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(DatabaseBaseModel.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(DatabaseBaseModel.metadata.drop_all)
    await engine.dispose()


@pytest.fixture
async def db_session_factory(db_engine):
    return async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)


@pytest.fixture
async def analysis_id(db_session_factory) -> int:
    async with db_session_factory() as session:
        record = CodeQualityAnalysis(
            trigger_type="pull_request_webhook",
            repository_full_name="testorg/testrepo",
            source_pr_number=1,
            source_pr_title="Test PR",
            source_pr_url="https://github.com/testorg/testrepo/pull/1",
            analysis_status="analyzing",
            initiated_at=datetime.now(timezone.utc),
        )
        session.add(record)
        await session.commit()
        await session.refresh(record)
        return record.id


async def _get_record(db_session_factory, analysis_id: int) -> CodeQualityAnalysis:
    async with db_session_factory() as session:
        return await session.get(CodeQualityAnalysis, analysis_id)


# Both poller functions import async_database_session_factory and
# application_settings via local imports, so we patch at the source module.
_PATCH_DB = "app.database.async_database_session_factory"
_PATCH_CFG = "app.config.application_settings"


def _mock_settings(interval: int = 0, timeout: int = 60) -> MagicMock:
    return MagicMock(
        session_poll_interval_seconds=interval,
        session_poll_timeout_seconds=timeout,
    )


# ---------------------------------------------------------------------------
# _process_completed_session unit tests
# ---------------------------------------------------------------------------

class TestProcessCompletedSession:
    """
    Unit-tests for _process_completed_session in isolation — no polling loop.
    """

    async def test_fix_pr_from_pull_requests_array_no_structured_output(
        self, db_session_factory, analysis_id
    ):
        """
        Core regression: session still running, structured_output is None,
        but pull_requests is populated. Should set fix_pr_created, not error.
        """
        payload = _session_payload(
            status="running",
            status_detail="working",
            pull_requests=[{"pr_url": "https://github.com/testorg/testrepo/pull/99"}],
            structured_output=None,
        )

        with patch(_PATCH_DB, db_session_factory):
            await _process_completed_session(analysis_id, payload)

        record = await _get_record(db_session_factory, analysis_id)
        assert record.analysis_status == "fix_pr_created"
        assert record.fix_pr_url == "https://github.com/testorg/testrepo/pull/99"
        assert record.completed_at is not None

    async def test_fix_pr_from_structured_output(
        self, db_session_factory, analysis_id
    ):
        """fix_pr_url in structured_output sets fix_pr_created and saves issues."""
        payload = _session_payload(
            status="exit",
            structured_output={
                "issues_found": True,
                "issues": [
                    {
                        "category": "DOC_DRIFT",
                        "file_path": "app/main.py",
                        "description": "Missing docstring",
                    }
                ],
                "fix_pr_url": "https://github.com/testorg/testrepo/pull/55",
                "summary": "1 issue found",
            },
        )

        with patch(_PATCH_DB, db_session_factory):
            await _process_completed_session(analysis_id, payload)

        record = await _get_record(db_session_factory, analysis_id)
        assert record.analysis_status == "fix_pr_created"
        assert record.fix_pr_url == "https://github.com/testorg/testrepo/pull/55"
        assert record.issue_count == 1
        issues = json.loads(record.issues_found)
        assert issues[0]["category"] == "DOC_DRIFT"

    async def test_fix_pr_structured_output_preferred_over_pull_requests(
        self, db_session_factory, analysis_id
    ):
        """When both sources exist, structured_output.fix_pr_url wins."""
        payload = _session_payload(
            status="exit",
            pull_requests=[{"pr_url": "https://github.com/testorg/testrepo/pull/10"}],
            structured_output={
                "issues_found": True,
                "issues": [],
                "fix_pr_url": "https://github.com/testorg/testrepo/pull/20",
                "summary": "done",
            },
        )

        with patch(_PATCH_DB, db_session_factory):
            await _process_completed_session(analysis_id, payload)

        record = await _get_record(db_session_factory, analysis_id)
        assert record.fix_pr_url == "https://github.com/testorg/testrepo/pull/20"

    async def test_no_issues_found(self, db_session_factory, analysis_id):
        """Session exits cleanly with no issues — status should be no_issues_found."""
        payload = _session_payload(
            status="exit",
            structured_output={
                "issues_found": False,
                "issues": [],
                "fix_pr_url": None,
                "summary": "No issues detected",
            },
        )

        with patch(_PATCH_DB, db_session_factory):
            await _process_completed_session(analysis_id, payload)

        record = await _get_record(db_session_factory, analysis_id)
        assert record.analysis_status == "no_issues_found"
        assert record.issue_count == 0

    async def test_session_error_status(self, db_session_factory, analysis_id):
        """Session ending with status=error sets analysis to error."""
        payload = _session_payload(status="error")

        with patch(_PATCH_DB, db_session_factory):
            await _process_completed_session(analysis_id, payload)

        record = await _get_record(db_session_factory, analysis_id)
        assert record.analysis_status == "error"
        assert "error" in record.error_message.lower()

    async def test_no_structured_output_and_no_pr_sets_error(
        self, db_session_factory, analysis_id
    ):
        """Session exits with no structured_output and no PRs — should be error."""
        payload = _session_payload(status="exit", structured_output=None)

        with patch(_PATCH_DB, db_session_factory):
            await _process_completed_session(analysis_id, payload)

        record = await _get_record(db_session_factory, analysis_id)
        assert record.analysis_status == "error"
        assert record.error_message is not None


# ---------------------------------------------------------------------------
# schedule_session_polling integration tests
# ---------------------------------------------------------------------------

class TestScheduleSessionPolling:
    """
    Tests for the full polling loop. The Devin API client is mocked to return
    a controlled sequence of session payloads, simulating the session lifecycle.
    """

    def _make_client(self, poll_responses: list) -> MagicMock:
        client = MagicMock()
        client.get_session_details = AsyncMock(side_effect=poll_responses)
        return client

    async def test_polling_resolves_when_pr_appears_while_session_running(
        self, db_session_factory, analysis_id
    ):
        """
        Simulates the real failure case: first two polls show session still
        running with no PR, third poll shows pull_requests populated while
        session is still running. Should resolve to fix_pr_created without
        waiting for session termination.
        """
        responses = [
            _session_payload(status="running", status_detail="working"),
            _session_payload(status="running", status_detail="working"),
            _session_payload(
                status="running",
                status_detail="working",
                pull_requests=[
                    {"pr_url": "https://github.com/testorg/testrepo/pull/7"}
                ],
            ),
        ]
        client = self._make_client(responses)

        with (
            patch(_PATCH_DB, db_session_factory),
            patch(_PATCH_CFG, _mock_settings()),
        ):
            await schedule_session_polling(analysis_id, "devin-test123", client)

        record = await _get_record(db_session_factory, analysis_id)
        assert record.analysis_status == "fix_pr_created"
        assert record.fix_pr_url == "https://github.com/testorg/testrepo/pull/7"
        # Should have stopped after 3 polls, not exhausted remaining responses
        assert client.get_session_details.call_count == 3

    async def test_polling_resolves_on_exit_status_with_no_issues(
        self, db_session_factory, analysis_id
    ):
        """Session transitions to exit with no issues — no_issues_found."""
        responses = [
            _session_payload(status="running", status_detail="working"),
            _session_payload(
                status="exit",
                structured_output={
                    "issues_found": False,
                    "issues": [],
                    "fix_pr_url": None,
                    "summary": "Clean",
                },
            ),
        ]
        client = self._make_client(responses)

        with (
            patch(_PATCH_DB, db_session_factory),
            patch(_PATCH_CFG, _mock_settings()),
        ):
            await schedule_session_polling(analysis_id, "devin-test123", client)

        record = await _get_record(db_session_factory, analysis_id)
        assert record.analysis_status == "no_issues_found"

    async def test_polling_resolves_on_suspended_status(
        self, db_session_factory, analysis_id
    ):
        """suspended is a terminal status and should be processed."""
        responses = [
            _session_payload(
                status="suspended",
                status_detail="inactivity",
                structured_output={
                    "issues_found": True,
                    "issues": [],
                    "fix_pr_url": "https://github.com/testorg/testrepo/pull/8",
                    "summary": "done",
                },
            ),
        ]
        client = self._make_client(responses)

        with (
            patch(_PATCH_DB, db_session_factory),
            patch(_PATCH_CFG, _mock_settings()),
        ):
            await schedule_session_polling(analysis_id, "devin-test123", client)

        record = await _get_record(db_session_factory, analysis_id)
        assert record.analysis_status == "fix_pr_created"

    async def test_polling_times_out(self, db_session_factory, analysis_id):
        """If the session never settles within timeout, mark as error."""
        client = MagicMock()
        client.get_session_details = AsyncMock(
            return_value=_session_payload(status="running", status_detail="working")
        )

        with (
            patch(_PATCH_DB, db_session_factory),
            # timeout=0 means the while condition is false immediately after
            # the first sleep, so the loop exits without processing
            patch(_PATCH_CFG, _mock_settings(timeout=0)),
        ):
            await schedule_session_polling(analysis_id, "devin-test123", client)

        record = await _get_record(db_session_factory, analysis_id)
        assert record.analysis_status == "error"
        assert "timed out" in record.error_message.lower()

    async def test_polling_continues_on_transient_api_error(
        self, db_session_factory, analysis_id
    ):
        """A single API failure should be swallowed; polling resumes and resolves."""
        responses = [
            Exception("network blip"),
            _session_payload(
                status="exit",
                structured_output={
                    "issues_found": False,
                    "issues": [],
                    "fix_pr_url": None,
                    "summary": "Clean",
                },
            ),
        ]
        client = MagicMock()
        client.get_session_details = AsyncMock(side_effect=responses)

        with (
            patch(_PATCH_DB, db_session_factory),
            patch(_PATCH_CFG, _mock_settings()),
        ):
            await schedule_session_polling(analysis_id, "devin-test123", client)

        record = await _get_record(db_session_factory, analysis_id)
        assert record.analysis_status == "no_issues_found"
