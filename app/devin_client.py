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
                            "DOC_DRIFT",
                            "COMPLEX_DEAD_CODE",
                            "INCONSISTENT_PATTERNS",
                            "INCOMPLETE_ERROR_HANDLING",
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
    "1. DOC_DRIFT \u2014 code behaviour that has diverged from its inline "
    "docs, README, or API docs. Be thorough: check route definitions, "
    "handler functions, middleware changes, schema/model changes that "
    "affect API contracts, and any auto-generated API specs. Compare "
    "every discovered endpoint against the documentation \u2014 identify "
    "endpoints that exist in code but are not documented, endpoints "
    "whose documentation is outdated (wrong parameters, response "
    "formats, status codes), and documentation for endpoints that no "
    "longer exist in code.\n"
    "2. COMPLEX_DEAD_CODE \u2014 code that is technically unreachable or "
    "unused but cannot be caught by simple linters. Examples: functions "
    "only called via dead paths, feature flags that are permanently off, "
    "entire modules that nothing imports transitively, methods overridden "
    "but never invoked through any call chain. Do NOT flag simple unused "
    "imports or variables \u2014 those belong to a linter.\n"
    "3. INCONSISTENT_PATTERNS \u2014 deviations from the dominant coding "
    "conventions in the repository. Examples: 90% of endpoints raise "
    "HTTPException but a few return raw dicts with status codes; most "
    "models use `created_at` but some use `date_created`; inconsistent "
    "naming, return types, or error shapes across sibling functions.\n"
    "4. INCOMPLETE_ERROR_HANDLING \u2014 functions that catch exceptions too "
    "broadly (e.g. bare `except Exception`), silently swallow errors, "
    "or omit error cases that sibling functions handle. Also flag async "
    "code missing proper cancellation/cleanup and API endpoints that "
    "return 500 instead of a meaningful error response."
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
            f"- Open a single fix PR against the main branch of "
            f"{repository_full_name}.\n"
            f"- Title the PR exactly (all lowercase): "
            f"\"devclean fix for pr #{pr_number} - [category] - [one-line description]\"\n"
            f"  where [category] is the lowercase version of the issue category "
            f"(e.g. doc_drift, complex_dead_code) and [one-line description] is a "
            f"concise summary of the fix.\n"
            f"- Structure the PR body using this exact format:\n"
            f"\n"
            f"---\n"
            f"# devclean fix for pr #{pr_number} - [category] - [one-line description]\n"
            f"\n"
            f"**Original PR / Trigger:** [{pr_url}]({pr_url})\n"
            f"\n"
            f"---\n"
            f"\n"
            f"**Number of fixes addressed:** [N]\n"
            f"\n"
            f"[For each fix, create a GitHub markdown anchor link in this list "
            f"that jumps to its full details section below:]\n"
            f"- Fix 1: [[type] - [short description]](#fix-1-type--short-description)\n"
            f"- Fix 2: [[type] - [short description]](#fix-2-type--short-description)\n"
            f"(and so on for each fix)\n"
            f"\n"
            f"---\n"
            f"\n"
            f"[Then, for each fix, add a full details section with this structure:]\n"
            f"\n"
            f"## Fix [N]: [type] - [short description]\n"
            f"\n"
            f"- **File:** `[file path]`\n"
            f"- **Issue:** [Full description of the issue]\n"
            f"- **Before:**\n"
            f"```\n"
            f"[code snippet before fix]\n"
            f"```\n"
            f"- **After:**\n"
            f"```\n"
            f"[code snippet after fix]\n"
            f"```\n"
            f"- **Impact:** [1-2 sentences explaining which of the 4 categories this "
            f"falls under and its impact on the codebase]\n"
            f"\n"
            f"---\n"
            f"\n"
            f"Be thorough: check for all four categories in the "
            f"PR diff."
        )

    @staticmethod
    def build_scan_prompt(
        repository_full_name: str,
        scan_path: str | None = None,
    ) -> str:
        if scan_path:
            scope_desc = (
                f"a folder audit of `{scan_path}` in "
                f"{repository_full_name}"
            )
            scan_instruction = (
                f"Scan only the folder `{scan_path}` in "
                f"{repository_full_name}."
            )
            thoroughness = (
                f"Be thorough: scan every file under "
                f"`{scan_path}`."
            )
        else:
            scope_desc = (
                f"a full repository scan of "
                f"{repository_full_name}"
            )
            scan_instruction = (
                f"Perform a full repository scan of "
                f"{repository_full_name}."
            )
            thoroughness = (
                "Be thorough: scan the entire codebase, "
                "not just the main entry point."
            )

        return (
            f"You are a code quality agent performing "
            f"{scope_desc}.\n"
            f"\n"
            f"{scan_instruction}\n"
            f"\n"
            f"Check for these four issue categories:\n"
            f"{ISSUE_CATEGORIES_BLOCK}\n"
            f"\n"
            f"If NO issues are found:\n"
            f"- Do not open any PR.\n"
            f"\n"
            f"If issues ARE found:\n"
            f"- Open a single fix PR against the main branch of "
            f"{repository_full_name}.\n"
            f'- Title the PR exactly: "code quality fix - adhoc - '
            f'[CATEGORY] - [one-line description]"\n'
            f"- Structure the PR body using this exact format:\n"
            f"\n"
            f"---\n"
            f"**Original PR / Trigger:** Adhoc scan\n"
            f"\n"
            f"## 1. TITLE\n"
            f"code quality fix — adhoc — [CATEGORY] — "
            f"[issue description]\n"
            f"Original PR that this is fixing or adhoc run: "
            f"Adhoc run\n"
            f"\n"
            f"## 2. WHAT\n"
            f"**No. of issues detected:** [N]\n"
            f"\n"
            f"[For each issue:]\n"
            f"- **File:** `[file path]`\n"
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
            f"[1-2 sentences: which of the 4 categories this falls "
            f"under and its impact on the codebase]\n"
            f"---\n"
            f"\n"
            f"{thoroughness}"
        )
