Add 'web' as a delivery platform for scheduled cron jobs, following the same architectural pattern as Telegram, Discord, and other platforms.

- `_KNOWN_DELIVERY_PLATFORMS`: register 'web' as a valid platform
- `_deliver_web()`: standalone function that POSTs job output to the web UI endpoint
- `_deliver_result()`: handle web delivery before target resolution
- `cronjob_tool` description: document 'web' as a deliver value

The web delivery endpoint receives the job output as JSON `{job_id, session_id, content}` and creates a Run record so the output appears in the web chat UI. If the target session was deleted, the UI auto-creates a new session.

## What does this PR do?

<!-- Describe the change clearly. What problem does it solve? Why is this approach the right one? -->

Adds `"web"` as a first-class delivery platform for cron jobs, matching the pattern used by `"telegram"`, `"discord"`, `"slack"`, etc. When a cron job has `deliver: "web"`, the scheduler POSTs the job output to a configurable web UI endpoint (`HERMES_WEB_UI_URL`, default `http://localhost:4000`). Unlike chat platforms, web delivery has no channel concept — the output is sent as an HTTP POST with the raw content.

## Related Issue

<!-- Link the issue this PR addresses. If no issue exists, consider creating one first. -->

Fixes #

## Type of Change

<!-- Check the one that applies. -->

- [ ] 🐛 Bug fix (non-breaking change that fixes an issue)
- [x] ✨ New feature (non-breaking change that adds functionality)
- [ ] 🔒 Security fix
- [ ] 📝 Documentation update
- [ ] ✅ Tests (adding or improving test coverage)
- [ ] ♻️ Refactor (no behavior change)
- [ ] 🎯 New skill (bundled or hub)

## Changes Made

<!-- List the specific changes. Include file paths for code changes. -->

- `cron/scheduler.py`: add `"web"` to `_KNOWN_DELIVERY_PLATFORMS`, add `_deliver_web()` with optional auth (`HERMES_WEB_DELIVERY_TOKEN`) and config.yaml support (`cron.web_ui_url`), modify `_deliver_result()` to handle web before target resolution, fix dead-code guard to report errors
- `tools/cronjob_tools.py`: add `'web'` to valid deliver options in the `deliver` parameter description
- `website/docs/user-guide/features/cron.md`: add `"web"` to the delivery options table

## How to Test

<!-- Steps to verify this change works. For bugs: reproduction steps + proof that the fix works. -->

1. Create a cron job with `deliver: "web"` via the API
2. Verify the scheduler POSTs to `POST /api/cron_deliveries` with `{job_id, session_id, content}`
3. Override the endpoint URL via `HERMES_WEB_UI_URL` env var or `cron.web_ui_url` in config.yaml
4. Add auth via `HERMES_WEB_DELIVERY_TOKEN` env var (optional)
5. `python3 -m pytest tests/cron/test_scheduler.py::TestWebDelivery -v` (10 tests)

## Checklist

<!-- Complete these before requesting review. -->

### Code

- [x] I've read the [Contributing Guide](https://github.com/NousResearch/hermes-agent/blob/main/CONTRIBUTING.md)
- [x] My commit messages follow [Conventional Commits](https://www.conventionalcommits.org/) (`feat(cron): add 'web' delivery platform for cron job output`)
- [x] I searched for [existing PRs](https://github.com/NousResearch/hermes-agent/pulls) to make sure this isn't a duplicate
- [x] My PR contains **only** changes related to this fix/feature (no unrelated commits)
- [x] I've run `pytest tests/cron/test_scheduler.py -q` and all tests pass
- [x] I've added tests for my changes (required for bug fixes, strongly encouraged for features)
- [x] I've tested on my platform: macOS 15.4 (ARM)

### Documentation & Housekeeping

<!-- Check all that apply. It's OK to check "N/A" if a category doesn't apply to your change. -->

- [x] I've updated relevant documentation (README, `docs/`, docstrings) — or N/A
- [ ] I've updated `cli-config.yaml.example` if I added/changed config keys — or N/A
- [ ] I've updated `CONTRIBUTING.md` or `AGENTS.md` if I changed architecture or workflows — or N/A
- [x] I've considered cross-platform impact (Windows, macOS) per the [compatibility guide](https://github.com/NousResearch/hermes-agent/blob/main/CONTRIBUTING.md#cross-platform-compatibility) — or N/A
- [x] I've updated tool descriptions/schemas if I changed tool behavior — or N/A

## For New Skills

<!-- Only fill this out if you're adding a skill. Delete this section otherwise. -->

N/A

## Screenshots / Logs

<!-- If applicable, add screenshots or log output showing the fix/feature in action. -->

```
Job 'abc123': delivered to web UI
Job 'abc123': web delivery target not found; the UI will auto-create it on next delivery
```
