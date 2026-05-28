from datetime import datetime, timezone

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class DatabaseBaseModel(DeclarativeBase):
    pass


class CodeQualityAnalysis(DatabaseBaseModel):
    __tablename__ = "analyses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    trigger_type: Mapped[str] = mapped_column(
        String(50), nullable=False
    )  # "pull_request_webhook" | "manual_trigger"

    repository_full_name: Mapped[str] = mapped_column(
        String(255), nullable=False, index=True
    )

    source_pr_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_pr_title: Mapped[str | None] = mapped_column(String(500), nullable=True)
    source_pr_url: Mapped[str | None] = mapped_column(String(500), nullable=True)

    devin_session_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    devin_session_url: Mapped[str | None] = mapped_column(String(500), nullable=True)

    fix_pr_url: Mapped[str | None] = mapped_column(String(500), nullable=True)

    analysis_status: Mapped[str] = mapped_column(
        String(50), nullable=False, default="pending", index=True
    )  # pending | analyzing | no_issues_found | fix_pr_created | error

    issues_found: Mapped[str | None] = mapped_column(Text, nullable=True)
    issue_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    initiated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
