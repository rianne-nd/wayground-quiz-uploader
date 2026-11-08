# Architecture

`wqu` publishes Quiz Toolkit `.xlsx` workbooks to Wayground (formerly
Quizizz) by driving a real Chromium browser with Playwright's **sync** API.
It is designed as a library first; the CLI is one consumer, a future GUI is
another.

## Layering

```
wqu/cli.py            the ONLY module that prints, prompts, or parses argv
   │
wqu/core.py           the public import surface (re-exports only)
   │
   ├── wqu/publish.py   publish_one() / publish_many(): per-file pipeline
   ├── wqu/assign.py    assign_one(): quiz page → homework → class → name →
   │                    No deadline → final Assign → gameplay page
   ├── wqu/session.py   Session: login/check/doctor/discover, browser lifetime
   ├── wqu/upload.py    load_xlsx()/preview_errors(): Part-1.5 validation
   ├── wqu/output.py    FileResult, format_links/format_json/summary, clipboard
   ├── wqu/selectors.py selector registry + Playwright interaction helpers
   ├── wqu/store.py     ~/.wayground/created.json tracking
   ├── wqu/errors.py    exception taxonomy (cli maps to exit codes)
   └── wqu/logging_setup.py  file logs under ./wqu-logs (max 50, rotated)
```

`wqu/vendor/wayground_upstream.py` is a verbatim MIT copy of littleyi22's
`wayground.py` (see NOTICE); nothing imports it at runtime.

## The GUI-ready contract (and how it is enforced)

1. **Core returns data; only `cli.py` prints.** All core functions return
   dataclasses (`FileResult`, `PublishResult`, `AssignResult`, `QuizData`…).
2. **Progress via callback.** Long-running functions accept
   `on_progress: Callable[[str], None] | None`; the CLI passes a stderr
   printer, a GUI would pass a progress bar.
3. **Cancellation via callback.** `publish_many` accepts
   `should_cancel: Callable[[], bool] | None`, checked at the top of each
   file loop iteration.
4. **No module-level mutable state.** Browser state lives in an explicit
   `Session` object; configuration in the `PipelineOptions` dataclass; both
   are passed as parameters.
5. **Sync Playwright, no asyncio.** Safe to run in a worker thread later.
6. **REPL-usable.** `from wqu.core import publish_one, PipelineOptions` works
   with no CLI, no prompts, no globals.

`tests/test_core_purity.py` mechanically asserts that core modules contain no
`print()` calls and never import `rich`/`typer`/`asyncio`;
`tests/test_acid.py` is the Part-2.6 acid test with a mocked browser.

## Per-file pipeline (`publish_many`)

```
for each file (should_cancel checked here):
  load_xlsx + validate ──(errors)──▶ FileResult(error="local validation failed…")
  duplicate-title check vs ~/.wayground/created.json (unless --force)
      ├─ skip  ▶ FileResult(skipped=True)
      ├─ resume ▶ open the existing editor
      └─ new   ▶ create: assessment page → "Start from scratch" → /edit
  record created quiz id in created.json
  resume with existing questions? skip import (prevents duplicates)
  otherwise: scroll → "Spreadsheet" → set_input_files → wait banner
      ├─ green "Looks good!"            ▶ click Import
      └─ red "Invalid correct option"   ▶ scrape rows (heuristic) + Cancel;
                                          continue only with --force-import
  publish modal: Name + Subject + Grade → modal Publish
      └─ note: an unchanged already-published quiz skips the modal entirely
  (decide_assign callback: CLI prompt / --assign / --no-assign / GUI)
  assign_one:  Assign → [accommodations glitch recovery] → class modal
               (tick Naic, force-uncheck accommodations, Next) →
               name modal (title, keep unchecked, Next) →
               Change → No deadline ON (state-read, never blind) →
               Save and close → verify "No deadline" on the page →
               final Assign (rightmost match) → gameplay page
  extract join link: DOM scan → "Copy Join link" + clipboard (sentinel-poisoned
                     so stale links can't leak between files) → DOM rescan
  FileResult(...) + created.json update
```

One browser + one context are shared across the loop; a Playwright trace is
kept for the run and saved to `wqu-logs/<ts>.trace.zip` on the first failure.

## Why DOM automation (Path B)

No HAR capture was available, so the API-first path could not be evaluated
without guessing endpoints — forbidden by the brief. The DOM path was chosen
explicitly; see `docs/api-notes.md`. The vendored upstream skill confirmed
Wayground exposes stable hooks; live probing then found real `data-testid`s
for most controls (see `docs/selectors.md`). Swapping individual steps to HTTP
calls later is a local change: the pipeline only calls `publish.py` /
`assign.py` functions.

## Hard-won behaviours (LIVE 2026-11-08)

- **aria-label overrides**: the top-bar Publish button's accessible name is
  "Publish quiz"; the modal X is "xmark"; several buttons expose icon aria
  names ("chevron-left", "copy", "trash").
- **Modals stack**: reaching the name-assignment step leaves the class modal
  mounted beneath it. Never wait for a previous modal to *hide*; wait for the
  *next* observable state.
- **The accommodations glitch**: the full-page "Set up learning supports"
  screen interferes right after the quiz-page Assign *or* after clicking
  "Select a class". Recovery = click the back arrow ("chevron-left"), then
  resume the flow (re-click Assign / let the modal open).
- **Default-checked checkbox**: "Set up Accommodations" flips to CHECKED as
  soon as a class row is selected (both modals) and must be force-unchecked.
- **Styled inputs**: checkboxes/switches are real `<input>` elements covered
  by decorative knobs; Playwright's actionability check times out, so
  `selectors.click_control()` falls back to a JS `el.click()`.
- **Lazily rendered sections**: "Import existing files" mounts only near the
  bottom of an *empty* editor and disappears once questions exist; resumed
  drafts with questions skip the import to avoid duplicates.
- **Republishing unchanged quizzes** navigates straight to the quiz page
  without showing the publish modal — treated as success.
- **Coachmarks** ("Sessions is now Reports → Got it") intercept clicks until
  dismissed.
- **Clipboard safety**: the clipboard is poisoned with a sentinel before
  clicking "Copy Join link" so a stale link from a previous file can never be
  mistaken for a fresh one. Links are validated against the join-link regex
  and never fabricated.

## Errors, exit codes, artifacts

`wqu/errors.py` defines the taxonomy; `publish_many` converts per-file
failures into `FileResult(error=…, error_kind=…)` and keeps going (unless
`--fail-fast`, or the kind is `session-expired`).

| Exit | Meaning |
|---|---|
| 0 | every file succeeded (skips count as success) |
| 1 | at least one file failed |
| 2 | configuration/session problem (run `wqu login`, class not found, bad args) |

Per failure: full-page screenshot `wqu-logs/<ts>-<file>.png` and (first
failure of the run) a Playwright trace `wqu-logs/<ts>.trace.zip` for
`trace.playwright.dev`. Logs rotate at 50 files.

## State on disk

| Path | Contents |
|---|---|
| `~/.wayground/state.json` | Playwright storage state (cookies) — never logged, never uploaded anywhere but wayground.com |
| `~/.wayground/created.json` | quizzes created/assigned by wqu (duplicate detection, resume, audit) |
| `./wqu-logs/` | logs, screenshots, traces |
| `./snaps/` | `wqu discover` DOM/aria/screenshot dumps |

## Detailed Playwright Flow & Functions

### Phase 1: Create & Publish (`wqu/publish.py` -> `publish_one()`)

1. **Navigate to Create:** `page.goto("https://wayground.com/activity/admin/assessment")`
2. **Dismiss Premium Popup:** click the `X` (`button[aria-label='xmark']`) if the
   "Unlock 45+ premium features" modal is visible.
3. **Click Start:** `page.get_by_text("Start from scratch").click()`
4. **Wait for Editor:** `page.wait_for_url("**/activity/admin/quiz/*/edit*")`
5. **Import Spreadsheet:** scroll to "Import existing files" → click
   "Spreadsheet" → `set_input_files()` on the modal's hidden input.
   (A resumed draft that already has questions skips this step — re-importing
   would duplicate them.)
6. **Validate Import:** wait for "Looks good!" or "Invalid correct option".
   If valid, click "Import"; if invalid, scrape the highlighted rows, cancel,
   save a screenshot and skip the file (unless `--force-import`).
7. **Open Publish Modal:** click `button[aria-label="Publish quiz"]`
   (the accessible name overrides the visible "Publish" text).
8. **Fill Metadata:** Name input (placeholder "Untitled Quiz") + Subject
   (`[data-testid=quiz-subject-select-box]`) + Grade
   (`[data-testid=publish-modal-grade-input-select-box]`), picking options
   from the `UL[role=listbox] > LI[role=option]` panels.
9. **Publish & Dismiss Success** (corrected after the 2026-11-08 headed run):
   * Click the modal's "Publish" button.
   * **While still on the `/edit` page**, wait for the "Great work!" modal.
   * Click the `X` (`button[aria-label='xmark']`) to dismiss it.
   * *Then* wait for the quiz page URL (drops `/edit`); if it does not come
     within 20 s the tool navigates to
     `https://wayground.com/activity/admin/quiz/{id}` directly.
   * Known alternative: if the quiz was already published and nothing changed,
     clicking Publish quiz skips the modal and navigates straight to the quiz
     page — treated as success.

### Phase 2: Assign (`wqu/assign.py` -> `assign_one()`)

1. **Ensure quiz page, then dismiss coachmarks (corrected order):** wait for
   `/activity/admin/quiz/*` → immediately check for and click the "Got it"
   tip ("Sessions is now Reports") → *then* click "Assign".
2. **Navigate to Assign:** click "Assign" on the quiz page.
3. **Wait for Homework Page:** `page.wait_for_url("**/activity/admin/quiz/homework/*")`
   (dismiss any coachmark again after landing).
4. **Handle Accommodations Glitch:** if the full-page "Set up learning
   supports" appears (after Assign *or* after "Select a class"), click the
   back arrow (`chevron-left` → "Back" → Escape → go_back) and resume.
5. **Standard Flow (With Class):**
   * Click "Select a class" (`[data-testid=select-class-button]`) → check
     "Naic" (click the row text) → force-uncheck "Set up Accommodations"
     (it flips ON when the class is selected;
     `[data-testid=assignment-setup-accommodations-toggle]`, JS-click fallback)
     → Next.
   * Fill the assignment Name → keep "Set up Accommodations" unchecked → Next.
   * Click "Change" (`[data-testid=toggle-calendar-button]`) → toggle
     "No deadline" ON (`input[role=switch][data-testid=date-time-selector-input]`,
     state read before clicking) → "Save and close"
     (`[data-testid=timeline-selector-continue-button]`) → verify the details
     row shows "No deadline".
   * Click the final **Assign** (rightmost match — the breadcrumb "Assign"
     sits on the left).
6. **Public Flow (Without Class)** (LIVE 2026-11-08: a quiz can be assigned
   publicly, no class needed): skip "Select a class", skip the name modal and
   skip the deadline "Change". Click the final **Assign** directly — the
   platform defaults to a public assignment with a 1-day deadline. This still
   produces a real join link.
7. **Extract Link:** on the gameplay page
   (`/gameplay/admin/activity/classic/*`), click "Copy Join link"; read the
   link from the DOM first, then the clipboard (sentinel-poisoned), and only
   accept a value matching the join-link regex.

`--keep-draft` bypasses Phase 2 entirely (publish only); `--no-assign` takes
the public flow so a join link is still produced.

### Library Manager (`wqu/library.py`)

Wayground free accounts cap *active* quizzes at 20 ("11 / 20 activities
created"). If active quizzes + new quizzes > 20:

1. Navigate to `https://wayground.com/my-library/admin/my-library/createdByMe`
2. Wait for the table/counter to load
   (`[data-testid=item-count-created-by-me-pill]` → "11 / 20").
3. Scrape all rows: `[data-testid=ui-table-row-{quiz_id}]` gives Title
   (`activity-card-title`), Age (relative text) and the Checkbox element
   (`ui-table-row-checkbox-{quiz_id}`, toggled via JS click — it is styled-covered).
4. If `--auto-archive-oldest N` is set, tick the N rows with the largest
   parsed age (`parse_age_minutes`; unknowns fall back to tail order).
5. Otherwise, the CLI prints a `rich.table` and uses `rich.prompt` to ask
   which to archive (`select_for_archive` parses `#,#`, `o<N>`, `a`, Enter).
6. Click the bulk bar's **Archive** button (`aria-label="Archive"`, which
   appears with "N activity selected"); accept a confirmation dialog if one
   appears; wait for the rows to detach.
7. Rescan; if the cap would still be exceeded, raise `LibraryLimitError`
   (exit 2) with guidance instead of flooding the account.

The pre-flight runs at the top of `publish_many()` on the same browser
session; any *read* failure is logged and skipped (never blocks a run), while
a real cap problem aborts cleanly. `wqu library` opens the same page headed
for manual management.

## Terminal UX & speed (v0.1.3)

- **Banner:** every command starts with a blue rich Panel showing the version.
- **Live step tree (`wqu/ui.py` → `LiveTreeUI`):** `publish` renders a
  continuously-updating `rich.live.Live` region on **stderr** (stdout stays
  reserved for the result block/JSON):

      Overall: ████████░░░░░░░░░░░░░░░░ 1/3 files (24.5s elapsed)

      ┌─ [2/3] DataComs_Mod15.xlsx ────────────────────────────────────┐
      │  Steps: ████████████████████░░░░ 28/33 (5.2s elapsed)          │
      │                                                                │
      │  ├─ ✓ Attaching workbook                            [00.8s]    │
      │  ├─ ⠸ Clicking final Assign                        [ 3.2s...]  │
      │  ├─ - Copying join link                           [ waiting ]  │
      │  └─ ...                                                        │
      └────────────────────────────────────────────────────────────────┘

  A catalog of ~33 expected steps (`ui.STEP_CATALOG`) is matched against the
  `on_progress` messages; ✓ green = done, ⠸ cyan = active (animates, timer
  ticks via `perf_counter`), `–` dim = skipped branch step, `-` dim = still
  pending, `✗` red + an indented `ERROR:` line = failed step (the core
  aborts that file; the tree freezes until the next `[i/n]` resets it).
  While a file is running the tree is windowed (`_KEEP_DONE` resolved steps +
  active + `_KEEP_PENDING` pending). When `FileTree.finish()` runs, the panel
  expands to the full catalog; `LiveTreeUI.on_file_result()` then stops the
  Live region so that complete timing report stays in the terminal, and the
  next `[i/n]` starts a fresh live region. Multi-file runs therefore leave one
  full step/timing box per file.
- **Rich wiring detail (v0.1.3 hotfix):** the live region is created as
  `Live(get_renderable=ui._render, ...)`, not `Live(ui._render, ...)`. Rich's
  positional argument expects a renderable; a bare callable is rendered as a
  plain object, so any `console.print()` while the region is active (e.g. the
  library pre-check before the first file) fails with
  `NotRenderableError: Unable to render <bound method LiveTreeUI._render ...>`.
  The callback form also re-evaluates spinner frames and timing cells on every
  refresh. Regression:
  `tests/test_ui.py::test_live_tree_ui_prints_preamble_while_live`.
- **Prompts vs Live (`[r]esume / [s]kip / [n]ew`, assign, archive):**
  every interactive prompt goes through `ui.pause()` … `input()` …
  `ui.resume()` (see `cli._prompt_line` and `make_choose_archive`), so the
  redraw can never mangle the prompt line. This replaces the v0.1.2
  workaround that disabled the live UI in interactive mode.
- **Non-TTY fallback:** when stderr is a pipe (logs, CI, tests), the UI emits
  plain styled lines instead of Live frames; file timings are still recorded
  for the summary table.
- **Result block without side borders:** the stdout panel uses
  `box=box.HORIZONTALS`, so double-click selection in PowerShell/Windows
  Terminal no longer includes `│` characters.
- **Summary table:** File / Status / **Time** / Join link (errors inline),
  followed by the `Processed …` caption. Clipboard copy happens before the
  block is printed.
- **Speed**: coachmark ("Got it") checks are instant `is_visible()` probes
  with a single 400 ms grace right after page load; the accommodations page
  is probed instantly and recovered with bounded ~1.2 s re-checks instead of
  a 10 s hidden-wait; the public assignment path clicks the final Assign
  immediately (no class modal waits).

## Output contract

- stdout: the `Label: URL` block (or `--json` array, or the dry-run plan).
- stderr: progress, warnings, the per-file summary, log path.
- Clipboard: the stdout block is copied in human mode only (never for
  `--json`, `--dry-run`, `--quiet`).
- Split labels normalize `_pt1` → `_Pt1` on output; natural sort orders
  `_pt1, _pt2, …, _pt10`.
