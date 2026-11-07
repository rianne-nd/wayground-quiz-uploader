# Architecture decision: DOM path (Path B), no HAR captured

Date: 2026-11-08
Status: decided, pending live verification of selectors

## What was captured

No `.har` file was provided for this build. Per Part 3 of the project brief,
that means the API-first option (Path A) could not be evaluated: there is no
evidence of Wayground endpoint shapes, auth header handling, CSRF tokens, or
`multipart/form-data` payload structure. Guessing endpoints was explicitly
forbidden, so Path A is off the table for v0.1.0.

The user selected the DOM path explicitly. Accordingly:

- **Upload**: Playwright `set_input_files()` against the file input inside the
  "Import questions from spreadsheet" modal. The modal parses the `.xlsx`
  client-side, so no `xlsx_to_json.py` conversion is needed (matches Part 3,
  DOM-path branch).
- **Publish**: DOM clicks in the "Great, you're almost done" modal.
- **Assign**: DOM clicks through the Assignment Details flow.
- **Join link**: DOM-first (input whose value matches the join-link regex),
  clipboard fallback via `navigator.clipboard.readText()`.

## Why DOM is still workable

Evidence that Wayground's front end exposes stable test hooks, from the
vendored upstream automation (`wqu/vendor/wayground_upstream.py`, MIT,
littleyi22/wayground-quiz-creator-skill):

- `[data-testid="auth-modal-welcome-title"]`, `[data-testid="continue-button"]`
- `[data-testid="create-resource-card"]`,
  `[data-testid="resource-creation-option-selector"]`
- `[data-testid="quiz-settings-modal"]`,
  `[data-testid="quiz-settings-modal-primary-button"]`,
  `[data-testid="publish-quiz-button"]`
- `[data-testid="edit-name-button"]`, `[data-testid="close-button"]`
- `[data-testid="question-type-item"]`, `[data-testid="save-question-button"]`

Caveat: those test IDs were observed for a *different* flow (manual question
entry via the editor, not the xlsx import path). They confirm the strategy of
`data-testid` → role/name → placeholder/text, but every selector used by this
project is marked `UNVERIFIED` in `wqu/selectors.py` until the first live run.

## Dependency consequences

- `httpx` is **not** added to dependencies (no API path).
- Playwright sync API only; no `asyncio` anywhere.

## Known unknowns to verify live (mirrors Part 14 of the brief)

1. The exact DOM shape of the three publish-modal dropdowns (Subject, Grade,
   Language) — custom listboxes; we use label-scoped lookup with ordered
   fallbacks.
2. The "No deadline" toggle's role (switch / checkbox / div) and how to read
   its checked state.
3. Whether "Select a class" is replaced by "Modify" when a class is already
   selected, and how the target row's checkbox is structured.
4. Whether "Set up Accommodations" is checked by default (screenshots say yes;
   we force-uncheck defensively).
5. Whether an unassigned (published-only) quiz exposes a join link via
   "Start now" / dashboard. If it does not, output carries the explicit
   `(NO JOIN LINK — published but not assigned)` marker; we never fabricate.
6. Whether the red "Invalid correct option" banner lists row numbers in the
   DOM; if not, the tool reports the banner text plus a screenshot.

## Upgrade path

If the user later provides `docs/wayground-flow.har` (gitignored), re-run the
analysis and move stable steps (upload/publish) to HTTP calls per Path A.
The pipeline is layered so per-step swaps are local: `publish.py` orchestrates
`upload.py` (validation) and DOM actions kept behind functions in
`publish.py`/`assign.py`/`selectors.py`.

## Update — 2026-11-08 (post-build, live)

The DOM path is fully implemented and **live-verified end-to-end** (headless)
against the real account: create → import → publish → assign → join link.
Live probing replaced most guesses with real hooks (`data-testid`s etc. —
see `docs/selectors.md`). For the record, the trace network log shows the
import flows do talk to `wayground.com/_api/main/upload-quiz` and
`_quizserver/main/v3/quiz/{id}/version/{vid}/questions`, and there is a quiz
metadata GET — useful hints if Path A is ever revisited with a HAR.
