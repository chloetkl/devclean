from app.devin_client import (
    CODE_QUALITY_STRUCTURED_OUTPUT_SCHEMA,
    DevinApiClient,
)


class TestDevinApiClientPromptGeneration:
    def test_pr_analysis_prompt_includes_pr_details(self):
        prompt = DevinApiClient.build_pr_analysis_prompt(
            repository_full_name="myorg/myrepo",
            pr_number=42,
            pr_title="Add user endpoint",
            pr_url="https://github.com/myorg/myrepo/pull/42",
        )
        assert "myorg/myrepo" in prompt
        assert "#42" in prompt
        assert "Add user endpoint" in prompt
        assert "DOC_DRIFT" in prompt
        assert "COMPLEX_DEAD_CODE" in prompt
        assert "INCONSISTENT_PATTERNS" in prompt
        assert "INCOMPLETE_ERROR_HANDLING" in prompt

    def test_scan_prompt_includes_repo_name(self):
        prompt = DevinApiClient.build_scan_prompt(
            repository_full_name="myorg/myrepo"
        )
        assert "myorg/myrepo" in prompt
        assert "full repository scan" in prompt.lower()
        assert "DOC_DRIFT" in prompt

    def test_pr_analysis_prompt_includes_pr_url(self):
        prompt = DevinApiClient.build_pr_analysis_prompt(
            repository_full_name="myorg/myrepo",
            pr_number=10,
            pr_title="Fix bug",
            pr_url="https://github.com/myorg/myrepo/pull/10",
        )
        assert "https://github.com/myorg/myrepo/pull/10" in prompt

    def test_scan_prompt_uses_adhoc_label(self):
        prompt = DevinApiClient.build_scan_prompt(
            repository_full_name="myorg/myrepo"
        )
        assert "adhoc" in prompt.lower()

    def test_scan_prompt_with_path_scopes_to_folder(self):
        prompt = DevinApiClient.build_scan_prompt(
            repository_full_name="myorg/myrepo",
            scan_path="src/utils",
        )
        assert "src/utils" in prompt
        assert "folder audit" in prompt.lower()
        assert "full repository scan" not in prompt.lower()

    def test_session_web_url_is_correct(self):
        client = DevinApiClient(
            api_token="test-token",
            organization_id="org-test",
            base_url="https://api.devin.ai",
        )
        url = client.build_session_web_url("abc123")
        assert url == "https://app.devin.ai/sessions/abc123"


class TestStructuredOutputSchema:
    def test_schema_has_required_fields(self):
        assert "properties" in CODE_QUALITY_STRUCTURED_OUTPUT_SCHEMA
        props = CODE_QUALITY_STRUCTURED_OUTPUT_SCHEMA["properties"]
        assert "issues_found" in props
        assert "issues" in props
        assert "fix_pr_url" in props
        assert "summary" in props

    def test_schema_required_list(self):
        required = CODE_QUALITY_STRUCTURED_OUTPUT_SCHEMA["required"]
        assert "issues_found" in required
        assert "summary" in required

    def test_issue_item_schema_has_category_enum(self):
        items_schema = CODE_QUALITY_STRUCTURED_OUTPUT_SCHEMA["properties"]["issues"]["items"]
        category_prop = items_schema["properties"]["category"]
        assert "enum" in category_prop
        assert "DOC_DRIFT" in category_prop["enum"]
        assert "COMPLEX_DEAD_CODE" in category_prop["enum"]
        assert "INCONSISTENT_PATTERNS" in category_prop["enum"]
        assert "INCOMPLETE_ERROR_HANDLING" in category_prop["enum"]
