from app.dashboard import _serialize_analysis_record
from app.models import CodeQualityAnalysis


class TestAnalysisRecordSerialization:
    def test_serialize_basic_analysis_record(self):
        record = CodeQualityAnalysis(
            id=1,
            repository_full_name="testorg/testrepo",
            source_pr_number=42,
            source_pr_title="Add endpoint",
            source_pr_url="https://github.com/testorg/testrepo/pull/42",
            trigger_type="pull_request_webhook",
            analysis_status="pending",
        )
        serialized = _serialize_analysis_record(record)
        assert serialized["id"] == 1
        assert serialized["repository_full_name"] == "testorg/testrepo"
        assert serialized["source_pr_number"] == 42
        assert serialized["analysis_status"] == "pending"
        assert serialized["trigger_type"] == "pull_request_webhook"

    def test_serialize_record_with_issues_json(self):
        record = CodeQualityAnalysis(
            id=2,
            repository_full_name="testorg/testrepo",
            trigger_type="manual_trigger",
            analysis_status="fix_pr_created",
            issues_found=(
                '[{"category": "DEAD_CODE", "file_path": "utils.py",'
                ' "description": "Unused import"}]'
            ),
            issue_count=1,
            fix_pr_url="https://github.com/testorg/testrepo/pull/100",
        )
        serialized = _serialize_analysis_record(record)
        assert serialized["issues_found"] == [
            {"category": "DEAD_CODE", "file_path": "utils.py", "description": "Unused import"}
        ]
        assert serialized["issue_count"] == 1
        assert serialized["fix_pr_url"] == "https://github.com/testorg/testrepo/pull/100"

    def test_serialize_record_with_invalid_json_returns_none(self):
        record = CodeQualityAnalysis(
            id=3,
            repository_full_name="testorg/testrepo",
            trigger_type="manual_trigger",
            analysis_status="error",
            issues_found="not-valid-json",
        )
        serialized = _serialize_analysis_record(record)
        assert serialized["issues_found"] is None

    def test_serialize_manual_trigger_has_no_pr_number(self):
        record = CodeQualityAnalysis(
            id=4,
            repository_full_name="testorg/testrepo",
            trigger_type="manual_trigger",
            analysis_status="analyzing",
        )
        serialized = _serialize_analysis_record(record)
        assert serialized["source_pr_number"] is None
        assert serialized["trigger_type"] == "manual_trigger"

    def test_serialize_record_with_duration(self):
        record = CodeQualityAnalysis(
            id=5,
            repository_full_name="testorg/testrepo",
            trigger_type="pull_request_webhook",
            analysis_status="no_issues_found",
            duration_seconds=120,
        )
        serialized = _serialize_analysis_record(record)
        assert serialized["duration_seconds"] == 120
        assert serialized["analysis_status"] == "no_issues_found"
