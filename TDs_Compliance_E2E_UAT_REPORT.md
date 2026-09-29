# TDS Compliance E2E CA UAT Report

## Initial discovery report — 28 September 2026

**Overall status: BLOCKED**  
**Recommendation: CA_UAT_NOT_READY**

## Environment

- Frontend tested in Chrome through Playwright: `http://127.0.0.1:3001`
- Backend: `http://127.0.0.1:8000/api`
- Backend health: `200`, database status `ok`
- No business workflow, evidence, statutory rule, or assignment data was changed during discovery.

## Results

| ID | Step | Expected | Actual | Status |
| --- | --- | --- | --- | --- |
| UAT-001 | Real UI login | Workspace opens | Opened as CA UAT Reviewer | PASS |
| UAT-002 | Local UI to API | Browser requests reach backend | Browser blocked `/api/runs` because port 3001 is absent from CORS allow-list | FAIL |
| UAT-003 | Configured UI API | Configured backend is reachable | Configured Cloudflare tunnel was unreachable | FAIL |
| UAT-004 | Backend health | API and database available | `/api/health` returned `200`, `database=ok` | PASS |

## Confirmed defects

1. **DEF-001 — BLOCKER:** the backend rejects the local UAT frontend origin (`http://127.0.0.1:3001`) with CORS. This prevents browser API calls.
2. **DEF-002 — BLOCKER:** `frontend/.env` points to an unreachable Cloudflare tunnel. The existing frontend on port 3000 therefore cannot conduct UAT against its configured service.

## Evidence

- `screenshots/00-entry.png`
- `screenshots/01-workspace.png`
- `screenshots/02-tds-overview.png`
- `probe.json`

External Google font requests were also blocked by the sandbox. They do not affect business workflow behavior.

## Not run

The assignment creation, ledger, calculation, deposits, interest, return audit, review decisions, correction return, lock, refresh/isolation, failure cases, and 1k/10k performance scenarios are blocked before the first authenticated API workflow request. No status is inferred for them.

## Required next action

Correct the isolated UAT runtime configuration only: run the local backend with `CORS_ORIGINS` allowing the local UAT frontend origin, then continue the same browser UAT. Do not alter statutory/compliance business logic to address these blockers.
## Retest after environment correction

An isolated backend process was started with the UAT browser origin allowed. The real browser then loaded `/tds-compliance`, obtained the assignment list, and loaded the selected assignment workspace successfully. This confirms the CORS defect is environmental and can be corrected without changing compliance logic.

However, the database currently exposes the pre-existing assignment `TDCA-24F1124BAA56`, rather than the required isolated synthetic UAT assignment. Reusing it would invalidate the requested data-isolation, fixture, expected-result, and persistence checks. This is recorded as **DEF-003 � BLOCKER**. The remainder of the UAT remains blocked until the schema-derived fixture pack and isolated assignments are created.
