# DevClean — Code Quality Remediation Agent

Event-driven automation that detects code quality issues in a GitHub repository and uses [Devin AI](https://devin.ai) to create fix PRs with structured issue documentation.

**[DevClean Dashboard](https://devclean.onrender.com/dashboard)** | **[Loom Demo](https://www.loom.com/share/377d86e5cb564678b98111154d93ec64)**

> **Try it out:** PRs can be created on [chloetkl/superset](https://github.com/chloetkl/superset) to test DevClean in action.

## Issue Categories

These categories target issues that require semantic understanding of the whole repository — issues linters can't catch.

| Code | Category | Description |
|------|----------|-------------|
| `DOC_DRIFT` | Documentation Drift | Code behaviour has diverged from its inline docs, README, or API docs |
| `COMPLEX_DEAD_CODE` | Complex Dead Code | Unreachable paths, permanently-off feature flags, transitively unused modules (not simple unused imports) |
| `INCONSISTENT_PATTERNS` | Inconsistent Patterns | Deviations from dominant coding conventions across the codebase |
| `INCOMPLETE_ERROR_HANDLING` | Incomplete Error Handling | Overly broad catches, silently swallowed errors, missing error cases that sibling functions handle |

## Architecture

```
┌──────────────────┐     ┌─────────────────────┐     ┌──────────────┐
│  GitHub Webhook   │────▶│  FastAPI Backend     │────▶│  Devin AI    │
│  (PR opened)      │     │                     │     │  Session     │
└──────────────────┘     │  ┌─────────────────┐ │     └──────┬───────┘
                         │  │ SQLite Database  │ │            │
┌──────────────────┐     │  └─────────────────┘ │     ┌──────▼───────┐
│  Manual Trigger   │────▶│                     │◀────│  Fix PR      │
│  (Dashboard/API)  │     │  ┌─────────────────┐ │     │  (on repo)   │
└──────────────────┘     │  │ Session Poller   │ │     └──────────────┘
                         │  └─────────────────┘ │
                         └─────────────────────┘
```

### Triggers

1. **PR Webhook** — On `pull_request.opened` from GitHub, the system analyses the diff for code quality issues.
2. **Manual Trigger** — A button on the dashboard (or `POST /api/repo/analyses`) kicks off a repository scan. An optional `scan_path` parameter lets you scope the audit to a specific folder instead of the full repo.

### Bot Loop Prevention

Any PR opened by `devin-ai-integration[bot]` is silently ignored to prevent feedback loops.

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/github/events` | GitHub webhook receiver |
| `POST` | `/api/repo/analyses` | Manual scan trigger (supports optional `scan_path`) |
| `GET` | `/api/analyses` | List all analyses (paginated) |
| `GET` | `/api/analyses/{id}` | Get single analysis |
| `POST` | `/api/analyses/{id}/retry` | Retry a failed analysis (re-spawns Devin session) |
| `GET` | `/api/statistics` | Dashboard statistics |
| `GET` | `/dashboard` | Web dashboard UI |
| `GET` | `/health` | Health check |

## Database Schema

Single `analyses` table:

| Column | Type | Description |
|--------|------|-------------|
| `id` | INTEGER | Primary key |
| `trigger_type` | TEXT | `"pull_request_webhook"` or `"manual_trigger"` |
| `repository_full_name` | TEXT | e.g. `owner/repo` |
| `scan_path` | TEXT | Folder path to scope the scan (optional) |
| `source_pr_number` | INTEGER | Source PR number (webhook only) |
| `source_pr_title` | TEXT | Source PR title |
| `source_pr_url` | TEXT | Source PR URL |
| `devin_session_id` | TEXT | Devin session identifier |
| `devin_session_url` | TEXT | Link to Devin session |
| `fix_pr_url` | TEXT | URL of the fix PR created by Devin |
| `analysis_status` | TEXT | `pending` / `analyzing` / `no_issues_found` / `fix_pr_created` / `error` |
| `issues_found` | JSON | Array of issue objects |
| `issue_count` | INTEGER | Number of issues detected |
| `error_message` | TEXT | Error details |
| `initiated_at` | DATETIME | When the analysis started |
| `completed_at` | DATETIME | When the analysis completed |
| `duration_seconds` | INTEGER | Computed on completion |

### Issue Object Shape

```json
{
  "category": "DUPLICATE_CODE",
  "file_path": "superset/views/core.py",
  "description": "Short description of the issue",
  "before": "code snippet before fix",
  "after": "code snippet after fix"
}
```

## Dashboard

The web dashboard at `/dashboard` provides:

- **Stat cards**: No Issues Found, Fix PR Created, Devin is working, Devin needs help
- **Secondary metrics**: Avg. resolution time, Error rate, Total analyses, Total issues detected (with category breakdown)
- **Analysis history table**: Trigger, Initiated At, Duration, Source PR, Devin Session, Fix PR, Status
- **Manual trigger form**: Repository input + "Run Audit" button

## Setup

### Prerequisites

- Python 3.11+
- A [Devin AI](https://devin.ai) account with API access

### Local Development

```bash
# Clone the repository
git clone https://github.com/chloetkl/devclean.git
cd devclean

# Install dependencies
pip install -e ".[dev]"

# Configure environment
cp .env.example .env
# Edit .env with your credentials

# Run the server
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DEVIN_API_TOKEN` | — | Devin AI API token |
| `DEVIN_ORGANIZATION_ID` | — | Devin organization ID |
| `DEVIN_API_BASE_URL` | `https://api.devin.ai` | Devin API base URL |
| `GITHUB_WEBHOOK_SECRET` | — | GitHub webhook secret for signature verification |
| `DATABASE_URL` | `sqlite+aiosqlite:///./data/devclean.db` | Database connection string |
| `SESSION_POLL_INTERVAL_SECONDS` | `30` | How often to poll Devin session status |
| `SESSION_POLL_TIMEOUT_SECONDS` | `1800` | Maximum time to wait for session completion |

### Docker

```bash
docker compose up --build
```

### GitHub Webhook Setup

1. Go to your repository → **Settings → Webhooks → Add webhook**
2. **Payload URL**: `https://your-host/api/github/events`
3. **Content type**: `application/json`
4. **Secret**: Generate with `openssl rand -hex 32`, set as `GITHUB_WEBHOOK_SECRET`
5. **Events**: Select "Pull requests"

## Development

```bash
# Run tests
pytest tests/ -v

# Run linter
ruff check app/ tests/

# Run server in development mode
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

## Tech Stack

- **Python 3.11+** with **FastAPI**
- **SQLAlchemy** (async) with **aiosqlite** (SQLite)
- **httpx** for async HTTP calls to Devin API
- **Jinja2** + **Tailwind CSS** for the dashboard
- **Pydantic Settings** for configuration management
