import logging

import httpx

from app.config import application_settings

logger = logging.getLogger(__name__)


def get_devin_api_client() -> "DevinApiClient":
    return DevinApiClient(
        api_token=application_settings.devin_api_token,
        organization_id=application_settings.devin_organization_id,
        base_url=application_settings.devin_api_base_url,
    )


CODE_QUALITY_STRUCTURED_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "issues_found": {
            "type": "boolean",
            "description": "Whether any code quality issues were detected",
        },
        "issues": {
            "type": "array",
            "description": "List of code quality issues detected",
            "items": {
                "type": "object",
                "properties": {
                    "category": {
                        "type": "string",
                        "enum": [
                            "DUPLICATE_CODE",
                            "DEAD_CODE",
                            "DOC_DRIFT",
                            "PLATFORM_BUG",
                        ],
                        "description": "Issue category",
                    },
                    "file_path": {
                        "type": "string",
                        "description": "Path to the file containing the issue",
                    },
                    "description": {
                        "type": "string",
                        "description": "Short description of the issue",
                    },
                    "before": {
                        "type": "string",
                        "description": "Code snippet before fix",
                    },
                    "after": {
                        "type": "string",
                        "description": "Code snippet after fix",
                    },
                },
                "required": ["category", "file_path", "description"],
            },
        },
        "fix_pr_url": {
            "type": ["string", "null"],
            "description": "URL of the fix PR if one was created",
        },
        "summary": {
            "type": "string",
            "description": "Human-readable summary of the analysis",
        },
    },
    "required": ["issues_found", "summary"],
}


ISSUE_CATEGORIES_BLOCK = (
    "1. DUPLICATE_CODE — identical or near-identical code blocks "
    "repeated across files or within the same file\n"
    "2. DEAD_CODE — unreferenced functions, variables, imports, or components\n"
    "3. DOC_DRIFT — code behaviour that has diverged from its inline docs, "
    "README, or API docs\n"
    "4. PLATFORM_BUG — logic errors or regressions that break functionality "
    "on mobile and/or desktop"
)


class DevinApiClient:
    """Async HTTP client for the Devin AI API (session creation and status polling)."""

    def __init__(self, api_token: str, organization_id: str, base_url: str) -> None:
        self._api_token = api_token
        self._organization_id = organization_id
        self._base_url = base_url.rstrip("/")
        self._http_client = httpx.AsyncClient(
            base_url=self._base_url,
            headers={
                "Authorization": f"Bearer {self._api_token}",
                "Content-Type": "application/json",
            },
            timeout=60.0,
        )

    async def create_code_quality_session(
        self,
        prompt: str,
        repository_full_name: str,
        session_tags: list[str] | None = None,
        session_title: str | None = None,
    ) -> dict:
        request_payload: dict = {
            "prompt": prompt,
            "repos": [repository_full_name],
            "tags": session_tags or ["devclean", "code-quality"],
            "structured_output_schema": CODE_QUALITY_STRUCTURED_OUTPUT_SCHEMA,
        }
        if session_title:
            request_payload["title"] = session_title

        response = await self._http_client.post(
            f"/v3/organizations/{self._organization_id}/sessions",
            json=request_payload,
        )
        response.raise_for_status()
        return response.json()

    async def get_session_details(self, session_id: str) -> dict:
        response = await self._http_client.get(
            f"/v3/organizations/{self._organization_id}/sessions/{session_id}",
        )
        response.raise_for_status()
        return response.json()

    def build_session_web_url(self, session_id: str) -> str:
        return f"https://app.devin.ai/sessions/{session_id}"

    async def close(self) -> None:
        await self._http_client.aclose()

    @staticmethod
    def build_pr_analysis_prompt(
        repository_full_name: str,
        pr_number: int,
        pr_title: str,
        pr_url: str,
    ) -> str:
        return (
            f"You are a code quality agent reviewing the diff of "
            f"PR #{pr_number} (\"{pr_title}\") in the repository "
            f"{repository_full_name}.\n"
            f"\n"
            f"Analyse only the diff of PR #{pr_number} (\"{pr_title}\"): {pr_url}\n"
            f"\n"
            f"Check for these four issue categories:\n"
            f"{ISSUE_CATEGORIES_BLOCK}\n"
            f"\n"
            f"If NO issues are found:\n"
            f"- Do not open any PR.\n"
            f"\n"
            f"If issues ARE found:\n"
            f"- Open a single fix PR against the main branch of {repository_full_name}.\n"
            f'- Title the PR exactly: "code quality fix - PR #{pr_number} - '
            f'[CATEGORY] - [one-line description]"\n'
            f"- Structure the PR body using this exact format:\n"
            f"\n"
            f"---\n"
            f"**Original PR / Trigger:** {pr_url}\n"
            f"\n"
            f"## 1. TITLE\n"
            f"code quality fix — PR #{pr_number} — [CATEGORY] — [issue description]\n"
            f"Original PR that this is fixing: {pr_title}\n"
            f"\n"
            f"## 2. WHAT\n"
            f"**No. of issues detected:** [N]\n"
            f"\n"
            f"[For each issue:]\n"
            f"- **File:** `[file path]` · Scope: [Mobile / Desktop / Mobile & Desktop]\n"
            f"  - **Before:**\n"
            f"```\n"
            f"[code snippet]\n"
            f"```\n"
            f"  - **After:**\n"
            f"```\n"
            f"[code snippet]\n"
            f"```\n"
            f"\n"
            f"## 3. WHY\n"
            f"[1-2 sentences: which of the 4 categories this falls under and its "
            f"impact on the codebase]\n"
            f"---\n"
            f"\n"
            f"Be thorough: check for all four categories in the PR diff."
        )

    @staticmethod
    def build_full_scan_prompt(repository_full_name: str) -> str:
        return (
            f"You are a code quality agent performing a full repository scan of "
            f"{repository_full_name}.\n"
            f"\n"
            f"Perform a full repository scan of {repository_full_name}.\n"
            f"\n"
            f"Check for these four issue categories:\n"
            f"{ISSUE_CATEGORIES_BLOCK}\n"
            f"\n"
            f"If NO issues are found:\n"
            f"- Do not open any PR.\n"
            f"\n"
            f"If issues ARE found:\n"
            f"- Open a single fix PR against the main branch of {repository_full_name}.\n"
            f'- Title the PR exactly: "code quality fix - adhoc - '
            f'[CATEGORY] - [one-line description]"\n'
            f"- Structure the PR body using this exact format:\n"
            f"\n"
            f"---\n"
            f"**Original PR / Trigger:** Adhoc full repository scan\n"
            f"\n"
            f"## 1. TITLE\n"
            f"code quality fix — adhoc — [CATEGORY] — [issue description]\n"
            f"Original PR that this is fixing or adhoc run: Adhoc run\n"
            f"\n"
            f"## 2. WHAT\n"
            f"**No. of issues detected:** [N]\n"
            f"\n"
            f"[For each issue:]\n"
            f"- **File:** `[file path]` · Scope: [Mobile / Desktop / Mobile & Desktop]\n"
            f"  - **Before:**\n"
            f"```\n"
            f"[code snippet]\n"
            f"```\n"
            f"  - **After:**\n"
            f"```\n"
            f"[code snippet]\n"
            f"```\n"
            f"\n"
            f"## 3. WHY\n"
            f"[1-2 sentences: which of the 4 categories this falls under and its "
            f"impact on the codebase]\n"
            f"---\n"
            f"\n"
            f"Be thorough: scan the entire codebase, not just the main entry point."
        )
