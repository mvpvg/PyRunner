# Changelog

All notable changes to PyRunner are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
The authoritative version number lives in `pyrunner/version.py`. This changelog
begins tracking at the current release; earlier history is in the git log.

## [Unreleased]

## [1.14.0] — July 15, 2026

### Added
- **AI Providers** — generic multi-provider AI integration. Saved provider
  profiles (Anthropic Claude, Z.AI GLM, OpenRouter, local Ollama, or any
  Anthropic-compatible endpoint), each with its own encrypted credential and
  default model; switch the active provider with one click. Existing Claude
  configurations migrate automatically (migration 0043). Connection tests are
  provider-aware: Anthropic runs a real web search, other providers run an MCP
  tool-call round-trip that validates the model's tool calling. Usage rows are
  attributed to the serving provider.
- **Channels** — messaging subsystem (Telegram first). Outbound messages from
  scripts via the `pyrunner_notify` helper and `notify_channels` routing;
  inbound webhook with a deny-by-default approval inbox, per-sender rate
  limits, and a dispatch worker (migrations 0039–0040).
- **Py AI** — a read-only in-app assistant that answers questions about this
  instance (scripts, runs, schedules, datastores) via in-process MCP tools.
  Available as a dashboard chat and as a Telegram channel handler
  (migration 0041).
- **Brand Tracker example plugin** — weekly brand-mention tracking (Serper
  web/news, Hacker News, Reddit) with AI sentiment analysis and credit caps.
- Whole-app Beta badge system driven by a single `IS_BETA` flag.
- Shared cache backend: `DatabaseCache` by default (no extra service), with an
  opt-in `REDIS_URL` to use Redis. Fixes rate-limiting and webhook dedup that
  previously used a per-process cache and were not shared across workers.
- `HEALTHCHECK` in the `Dockerfile` (mirrors docker-compose) so `docker run`
  users also get container health reporting.
- Brotli-compressed static assets via WhiteNoise (`.br` in addition to gzip).
- CI: GitHub Actions (tests + Ruff), pre-commit hooks, Dependabot, editorconfig,
  and open-source community files.

### Changed
- Expanded `.env.example` to document the previously-undocumented settings
  (HSTS, DB connection age, gunicorn/PORT, login/API rate limits, per-run
  resource limits).
- README: the Claude AI section is now "AI in your scripts" with a provider
  table and per-provider setup notes.

### Security
- Removed passwordless magic-link login; fixed a channels XSS.
- Gated global-settings handlers, environment/package management, and
  application-log reads on superuser.
- Reject pip option lines in bulk requirements install.
- Added a scoped Content-Security-Policy header (defense-in-depth).

## [1.13.0]

Headline capabilities available in this release:

- **Script management** — create, edit, schedule, and monitor Python scripts.
- **Environments** — isolated per-script virtualenvs with custom pip packages.
- **Secrets** — encrypted environment variables and secrets (Fernet).
- **Claude AI for scripts** — call Claude from scripts (`pyrunner_ai`) using a
  Claude subscription token or an Anthropic API key.
- **Channels** — outbound + inbound messaging (Telegram) with an approval inbox.
- **Py AI** — a read-only in-app Claude assistant over your scripts/runs.
- **Plugins** — installable, self-provisioning Django-app plugins with an SDK.
- **Sandbox & isolation** — opt-in bubblewrap/rlimit isolation for runs.
- **Tenancy** — workspaces with scoped resources and RBAC.
- **Postgres support** — opt-in via `DATABASE_URL`; SQLite stays the default.
- **Notifications** — email, webhook, and Telegram alerts on completion/failure.
