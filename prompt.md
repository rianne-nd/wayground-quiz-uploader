# Project: wayground-quiz-uploader (CLI name: `wqu`)

Build a Python CLI tool that takes one or more `.xlsx` files produced by an
existing PowerShell quiz-converter toolkit ("Quiz Toolkit"), and drives a real
Chromium browser (Playwright) — or a direct HTTP client if the network capture
permits — to: upload each quiz to Wayground (formerly Quizizz), publish it,
optionally assign it to a class, and return a formatted join-link string.

This is a personal-automation tool for a single user on Windows. It is not a
SaaS product, not multi-tenant, not a web service.

The architecture must be GUI-ready from day one (see Part 2.6), but **no GUI is
to be built in this project**. The core must simply be usable as a Python
library so a GUI can be added later as a thin wrapper — without rewriting any
of the automation code.

---

## Part 1 — What the tool must do (user's manual workflow)

The user already runs a PowerShell toolkit that turns AI-formatted text into
Quizizz-ready `.xlsx` workbooks. **That toolkit is out of scope — do not modify
it.** This project starts where it ends: with an `.xlsx` on disk.

The user's manual flow today (this is the ground truth; screenshots exist in
the repo at `docs/screenshots/` if provided):

1. Go to `https://wayground.com/activity/admin/assessment`.
2. Click **"Start from scratch"**. App redirects to
   `https://wayground.com/activity/admin/quiz/{quizId}/edit?fromCreateFlow=true`.
3. On the editor, scroll to **"Import existing files"** and click
   **"Spreadsheet"**. A modal **"Import questions from spreadsheet"** opens with
   a drag-and-drop area and an **"Upload from device"** button.
4. Select the `.xlsx`. The modal shows a preview table with columns:
   `Question`, `Question Type`, `Option 1`–`Option 5`, `Correct`, `Time`,
   `Image link`, `Answer explanation`, plus a delete button per row.
   - Green banner **"Looks good!"** = no errors.
   - Red banner **"Invalid correct option"** = at least one row is broken.
5. Click **"Import"** in the modal. The editor now shows the questions.
6. Click **"Publish"** (top right). Modal **"Great, you're almost done"**:
   - **Name** input (placeholder `Untitled Quiz`, max 64 chars)
   - **Subject** dropdown (default `Select...`; user picks
     `Information Technology (IT)`)
   - **Grade** dropdown (default `Select...`; user picks `University`)
   - **Language** dropdown (leave `English`)
   - **Visibility** radio group (leave `Publicly visible`)
   - **Teaching goal** tag buttons (leave all off)
   - **Cancel** / **Publish**
7. Click **"Publish"**. App navigates to
   `https://wayground.com/activity/admin/quiz/{quizId}`.
8. Click **"Assign"** (top right). App navigates to
   `https://wayground.com/activity/admin/quiz/homework/{homeworkId}` —
   the **"Assignment Details"** page.
9. In the **"Start – End time"** row, click **"Change"**. A modal opens with a
   **"No deadline"** toggle. Enable it, click **"Save and close"**.
10. **Branch point — the user wants to be asked:**
    *"Assign to a class? [Naic] / Skip"*

    - **Whole quizzes:** choose `Naic`. Modal **"Assign to a class"** opens; tick
      the `Naic` row (shows "12 students · 12 completed sessions"), click
      **"Next"**. Modal becomes **"Name this assignment (Optional)"**; fill the
      title with the quiz name, **uncheck** "Set up Accommodations" (it is
      **checked by default** in the screenshots — always explicitly uncheck it),
      click **"Next"**. Back on the assign page, click **"Assign"** (top right).
    - **Split quizzes:** choose `Skip`. The user does not assign split parts to
      a class. If Wayground still exposes a join link for an unassigned quiz,
      the tool must capture it. If not, print the quiz URL with an explicit
      `(NO JOIN LINK — published but not assigned)` marker. Never fabricate.

11. After assigning, the app opens
    `https://wayground.com/gameplay/admin/activity/classic/{activityId}`.
    Click **"Copy Join link"**. Clipboard now contains something like
    `https://wayground.com/join?gc=24923411&source=liveDashboard`.
12. The user formats the result as:

    ```
    QuizName: https://wayground.com/join?gc=24923411&source=liveDashboard
    ```

    For split parts (files named `*_pt1.xlsx`, `*_pt2.xlsx`, …):

    ```
    QuizName_Pt1: https://wayground.com/join?gc=...&source=liveDashboard
    QuizName_Pt2: https://wayground.com/join?gc=...&source=liveDashboard
    ```

    Note: filenames use lowercase `_pt1`; the **output label uses `_Pt1`**
    (capital P). Enforce this normalization.

---

## Part 1.5 — Input contract (what you need from the user)

**Do not ask for the entire PowerShell toolkit repo.** Do not clone or read it
even if a link is provided. That repo is a different language, a different
scope, and pulling it into context is expensive and risks the agent wandering
off-task.

Instead, work from two artifacts:

1. **One redacted sample `.xlsx`** — 3–4 questions, both multiple-choice and
   (if available) a fill-in-the-blank, saved as
   `tests/fixtures/sample_single.xlsx`. If the user cannot provide one, generate
   it with `openpyxl` matching the spec below and confirm with the user.
2. **A short format excerpt** (below) — that's all the format knowledge needed.

Spec — every input workbook:

- Has **exactly one sheet**, named `Create a Quiz`.
- Row 1 is the header, bold, frozen.
- 11 columns in this exact order:

  | # | Header |
  |---|---|
  | 1 | Question Text |
  | 2 | Question Type |
  | 3 | Option 1 |
  | 4 | Option 2 |
  | 5 | Option 3 |
  | 6 | Option 4 |
  | 7 | Option 5 |
  | 8 | Correct Answer |
  | 9 | Time in seconds |
  | 10 | Image Link |
  | 11 | Answer explanation |

- `Question Type` is one of `Multiple Choice`, `Checkbox`,
  `Fill-in-the-Blank` (spelling variants exist).
- `Correct Answer` is a comma-separated list of **1-based** option indices
  (e.g., `"2"` or `"1,3"`), or blank for Fill-in-the-Blank (accepted answers
  live in the Option columns).
- `Image Link` and `Answer explanation` may be blank or the literal string
  `Optional`.

The **filename** also carries meaning:

- Trailing `Quizizz_` prefix is added by the toolkit and **must be stripped**
  when deriving the quiz title (`Quizizz_DataComs_Mod14.xlsx` →
  `DataComs_Mod14`).
- Trailing `_pt<N>` marks a split part (`_pt1`, `_pt2`, …, `_pt10`) — see
  Part 1 step 12.

That's the entire input contract. No other files are required from the user.

---

## Part 2 — Ground rules (non-negotiable)

### 2.1 Attribution and vendoring
- Copy `wayground.py` from
  `https://github.com/littleyi22/wayground-quiz-creator-skill` (MIT) into
  `wqu/vendor/wayground_upstream.py`. Preserve the original MIT header at the
  top of the file.
- Add `NOTICE` crediting the original author.
- Add `LICENSE` (MIT) for this project.
- Do **not** fetch the upstream repo at runtime. Vendor the file.

### 2.2 Do not touch the user's PowerShell toolkit
Never modify, refactor, or "improve" any file outside this repo. That includes
`Quiz Toolkit.cmd`, `Convert-Quizzes.ps1`, `QuizToolkit.ps1`, or anything under
`Quizzes\`. This repo consumes `.xlsx` files; it does not produce them.
Do not clone or read that repo either — see Part 1.5.

### 2.3 Chunk long writes (timeout protection) — CRITICAL
The user's AI agent **times out if a single file write is too long**. Enforce:

- **No single Write/Edit call may exceed ~150 lines.** Break larger files into
  sequential edits.
- When creating a file over ~100 lines, use this pattern:
  1. **Write** the file with a skeleton (imports, top-level structure, `# TODO`
     markers) — 30–60 lines.
  2. **Edit** to fill the first section.
  3. **Edit** to fill the next section.
  4. Continue until done.
- Specifically for **README.md, ARCHITECTURE.md, docs/*.md, and any source file
  > 200 lines**, split the write into at least 3 separate edits. Suggested
  README split: (a) title + pitch + badges, (b) install + quickstart,
  (c) command reference, (d) relationship-to-toolkit + limitations + security +
  license.
- If an edit is rejected due to "string too long" or times out, retry with a
  smaller chunk — do not attempt the same large write twice.
- After every successful write, append a line to `PROGRESS.md` (see 2.4).

### 2.4 `PROGRESS.md` resume checkpoint — CRITICAL
Create `PROGRESS.md` at the repo root on the very first step. Format:

```
# Build progress

- [ ] 0. Scaffold repo, pyproject, CI, LICENSE, NOTICE, .gitignore
- [ ] 1. HAR capture → docs/api-notes.md
- [ ] 2. Vendor wayground.py
- [ ] 3. wqu login / wqu check / wqu doctor
- [ ] 4. wqu publish --dry-run
- [ ] 5. wqu publish end-to-end (create + publish, no assign)
- [ ] 6. wqu assign (No deadline → Naic → name → uncheck → Assign)
- [ ] 7. Join-link extraction (DOM + clipboard fallback)
- [ ] 8. wqu publish --assign end-to-end
- [ ] 9. Split-file output formatting + natural sort
- [ ] 10. Tests + CI green
- [ ] 11. README + ARCHITECTURE + docs
- [ ] 12. Tag v0.1.0
```

After finishing each step (and verifying it — see Part 7), tick the box and add
a one-line note: `- [x] 4. wqu publish --dry-run — OK on sample_single.xlsx`.
**When resuming after a timeout, read `PROGRESS.md` first** and continue from
the first unticked item. Do not restart from scratch.

### 2.5 Don't over-engineer
- No async (`asyncio`), no ORM, no plugin system, no config-file format, no web
  UI, no database.
- No dependency unless it earns its place. Allowed: `playwright`, `openpyxl`,
  `httpx` (only if the API path is chosen), `typer` or plain `argparse`,
  `rich`, `pytest`, `ruff`.
- Windows is the primary target. macOS/Linux support is a bonus.
- Never use `time.sleep()` to paper over a race. If the flow is flaky, add a
  `wait_for_*` on the observable state that indicates the transition finished
  (URL change, modal appearing, banner text).

### 2.6 GUI-ready core shape (cheap insurance, not GUI scaffolding) — CRITICAL

A GUI may be added in the future as a **separate, thin wrapper**. Enforce the
following now, at ~30 minutes of extra care. Do not skip them, and do not
deviate on the assumption that "we can refactor later" — you cannot, cheaply.

**Non-negotiable rules:**

1. **Core returns data; only `cli.py` prints.**
   `publish.py`, `assign.py`, `upload.py`, `session.py`, `output.py` must be
   usable as a Python library with zero `print()` calls. They return dataclasses
   (`PublishResult`, `AssignResult`, etc.). Only `wqu/cli.py` writes to stdout
   or stderr. If a progress message needs to appear, it goes through the
   callback in rule 2.

2. **Progress via callback, not `print()` inside core.**
   Every long-running public function accepts:
   ```python
   on_progress: Callable[[str], None] | None = None
   ```
   The CLI passes `lambda msg: print(msg, file=sys.stderr)`. A future GUI passes
   `lambda msg: progress_bar.update(msg)`. The core never imports `rich` or
   `typer`.

3. **Cancellation via callback, checked between files.**
   Every multi-file public function accepts:
   ```python
   should_cancel: Callable[[], bool] | None = None
   ```
   Check it at the **top of each file loop iteration**, not mid-step. Do not
   thread cancel tokens through every helper. The CLI doesn't need it; a future
   GUI wires it to a Cancel button. Adding it now is ~5 lines.

4. **No module-level mutable state.**
   No `_browser = None`, no `_config = {...}`, no `os.environ` reads scattered
   across files. Session is an object. Config is a `PipelineOptions` dataclass.
   Both are passed explicitly.

5. **Sync Playwright, not `async`.**
   Sync Playwright wrapped in a worker thread is exactly what a future GUI
   needs. `async` adds debugging pain for zero benefit here. Do not introduce
   `asyncio`.

6. **Core is REPL-usable.**
   The following must work with no CLI, no prompts, no globals:
   ```python
   >>> from wqu.core import publish_one, PipelineOptions
   >>> from pathlib import Path
   >>> r = publish_one(Path("sample.xlsx"),
   ...                 PipelineOptions(assign_class="Naic"))
   >>> r.join_link
   'https://wayground.com/join?gc=24923411&source=liveDashboard'
   ```
   This is the acid test. Add it as a doctest or a unit test.

**Suggested module layout** (one public entry point per concern):

```
wqu/
├── core.py          # the ONLY importable public API; re-exports below
├── session.py       # Session class; login(), check(), doctor()
├── upload.py        # load_xlsx(), preview_errors()
├── publish.py       # publish_one(), publish_many()
├── assign.py        # assign_one()
├── output.py        # format_links()
├── selectors.py     # selector constants (DOM path only)
├── logging_setup.py # logging config
├── cli.py           # the ONLY place that prints / prompts
└── vendor/
```

**Do NOT build a GUI now. Do NOT add GUI scaffolding. Do NOT add a `--gui`
flag.** Just enforce these rules so a GUI is a ~30-line wrapper later.

---

## Part 3 — Architecture decision: API-first, DOM fallback

**This is the most important decision in the project. Do it before writing any
browser automation code.**

Two possible implementations exist for the upload/publish/assign steps:

**Path A — HTTP API (preferred).** If the HAR capture (Part 6) shows clean
`POST` requests with stable JSON or `multipart/form-data` bodies, call those
endpoints directly with `httpx` using the session cookies. This is smaller,
faster, and survives frontend deploys.

**Path B — DOM automation (fallback).** Playwright drives a real browser and
clicks the same elements the user clicks. This is larger and more brittle, but
it works regardless of backend API details.

**You must capture the HAR first (Part 6).** Then write `docs/api-notes.md`
stating which path you're taking and why. Then implement.

- If **API path**: `xlsx_to_json.py` is **only** required if the API rejects the
  raw xlsx. Most likely the API accepts `multipart/form-data` with the xlsx —
  in which case, skip xlsx_to_json entirely.
- If **DOM path**: implement `xlsx → Playwright setInputFiles()` — the modal
  parses the xlsx client-side, no conversion needed.
- It is acceptable to mix: e.g., API for upload/publish, DOM for assign. Choose
  per-step based on the HAR.

Whatever path is chosen, still build `wqu doctor` and `wqu discover` (below),
and still follow the selector-discipline rules in Part 5 for any DOM step.

---

## Part 4 — CLI surface

Commands:

```
wqu login                      # interactive login; saves ~/.wayground/state.json
wqu check                      # verify stored session still works
wqu doctor                     # diagnose environment (see below)
wqu discover <url>             # open URL headed, dump DOM/aria/screenshot to ./snaps/
wqu publish <path> [<path>...] # files or folders; full pipeline
```

Flags for `publish`:

| Flag | Default | Meaning |
|---|---|---|
| `--subject "Information Technology (IT)"` | that value | subject for publish modal |
| `--grade University` | `University` | grade for publish modal |
| `--title <name>` | xlsx filename (strip `Quizizz_`, strip `.xlsx`) | quiz title |
| `--assign <class>` | — | force-assign to this class name |
| `--no-assign` | — | never assign; also suppress prompt |
| `--no-deadline` | on | set "No deadline" (still expose `--deadline` for future) |
| `--no-headless` | off | show browser |
| `--dry-run` | off | validate only, no browser |
| `--json` | off | machine output |
| `--quiet` | off | only the `Label: URL` line(s) |
| `--keep-draft` | off | publish, skip assign entirely |
| `--force` | off | allow duplicate title |
| `--fail-fast` | off | stop on first file error (default: skip and continue) |
| `--force-import` | off | click Import despite preview errors (dangerous; log loudly) |
| `--interactive-review` | off | pause after each publish, show URL, wait for Enter before assign |
| `--yes` | off | non-interactive; takes defaults for all prompts |
| `--from-toolkit-root <path>` | — | auto-discover `Quizzes\*\_QuizizzOutput\*.xlsx` |

Interactive behavior when neither `--assign` nor `--no-assign` is passed:

```
[1/3] Quizizz_DataComs_Mod14.xlsx
      Title: DataComs_Mod14
      Assign to class "Naic"? [y/N]:
```

For split parts (`*_pt*.xlsx`), default to `N` and annotate:
`(split part — default N)`.

If stdin is not a TTY and no `--assign`/`--no-assign`/`--yes` was given:
default to `N` with a warning to stderr. **Never hang waiting for input in a
non-interactive context.**

### Output rules (strictly)
- All status/progress/log messages → **stderr**.
- Final formatted `Label: URL` block (or `--json` payload) → **stdout**.
- Human format block is copied to clipboard. **Do not** copy in `--json`,
  `--dry-run`, or `--quiet` modes.
- `--json` schema:
  `[{"file": "...", "title": "...", "quiz_url": "...", "join_link": "..." | null, "assigned": true|false, "error": null|"..."}]`
- Split parts must sort naturally within a group: `_pt1, _pt2, ..., _pt10`
  (not `_pt1, _pt10, _pt2`).
- Output label normalizes to `_Pt<N>` regardless of filename case.

### `wqu doctor` checks
- Python version ≥ 3.11
- `playwright` importable; Chromium installed (`playwright install chromium`)
- `~/.wayground/state.json` exists; not obviously stale
- Network reachability to `wayground.com` (HEAD request, 3s timeout)
- `wqu-logs/` writable
- `openpyxl` importable

### `wqu discover <url>` behavior
- Launch Playwright headed with the stored session.
- Navigate to `<url>`, print "Interact now, Ctrl+C when done".
- On Ctrl+C: save `./snaps/<timestamp>-page.html` (`page.content()`),
  `./snaps/<timestamp>-aria.txt` (`page.locator('body').aria_snapshot()`),
  `./snaps/<timestamp>-shot.png` (full-page screenshot).
- Close browser, print the paths.
- This is the canonical way to find selectors without DevTools fumbling.

---

## Part 5 — Selector & interaction discipline (for DOM path)

- **Never select by CSS class.** Wayground uses hashed class names
  (`css-1x8y2z`, `sc-abc123`). Use only, in this order:
  1. `data-testid` (if present)
  2. `role` + accessible name: `page.get_by_role("button", name="Publish")`
  3. `placeholder` / `aria-label`
  4. Visible text: `page.get_by_text("Start from scratch", exact=False)`
  5. Structural position inside `[role=dialog]` / `[role=listbox]` — last resort
- **Modals and dropdowns render at the end of `<body>`.** All selectors must be
  document-wide, not scoped to the triggering element.
- **Multiple matches are expected.** Wayground has two "Publish" buttons on
  screen (top bar and modal). Disambiguate by scoping:
  `page.get_by_role("dialog").get_by_role("button", name="Publish")`.
- **Wait on observable state, never `sleep()`.**
  `page.wait_for_url(...)`, `page.wait_for_selector(...)`,
  `expect(locator).to_be_visible()`.
- **Banner detection** — wait for green OR red:
  `page.locator('text=/looks good/i').or_(page.locator('text=/invalid correct option/i')).first.wait_for()`
- **Centralize every selector** in `wqu/selectors.py` as a typed constant.
  Comment each with the date last verified. Example:
  ```python
  START_FROM_SCRATCH = ("role", "button", "Start from scratch")  # verified 2026-11-08
  ```
- **Browser context settings:** always pass
  `locale="en-US", timezone_id="Asia/Manila", viewport={"width": 1440, "height": 900}`.
  Some React forms do not render correctly at default 1280×720 in headless mode.
- **Playwright trace on failure:** wrap the run in
  `context.tracing.start(screenshots=True, snapshots=True, sources=True)`;
  on any exception, `context.tracing.stop(path=f"wqu-logs/{run_id}.trace.zip")`.
  Print the path. This produces a `.zip` the user can drop into
  `trace.playwright.dev` for debugging.

---

## Part 6 — Step 0: HAR capture (do this before any code)

The user will capture a `.har` file by walking through the flow manually once.
Instructions to give the user:

1. Open Chrome/Edge DevTools → **Network** → **Fetch/XHR**.
2. Click the 🚫 clear button.
3. Walk the entire manual flow: create → upload xlsx → import → publish →
   assign → copy join link.
4. Right-click any request → **"Save all as HAR with content"** →
   save as `docs/wayground-flow.har` (add to `.gitignore` — it contains
   session cookies; do not commit it).
5. Also open DevTools → **Application → Cookies** and note the names of
   cookies scoped to `wayground.com`.

Before writing any code, analyze the HAR:

- Grep for `import`, `publish`, `homework`, `assign`, `activity`, `gc=` — these
  are likely endpoint path fragments.
- Look for `POST` requests with `Content-Type: multipart/form-data` — the
  upload endpoint.
- Look for `POST` with `application/json` — likely create/publish/assign.
- Look for `csrf`, `X-CSRF-Token` headers — if present, need to extract and
  re-send.
- Look for `Authorization: Bearer ...` — if present, need to lift the token from
  `localStorage` (Playwright's `storage_state` captures it).

Write findings into `docs/api-notes.md`. **Choose Path A (API) or Path B (DOM)
based on what's actually there.** If ambiguous, default to DOM for the fragile
steps and API for the stable steps.

If the user does not provide a HAR by the time you're ready to implement, ask
for it explicitly before proceeding. **Do not guess endpoint shapes.**

---

## Part 7 — Implementation order (each step ends with verification)

Follow this order. **After each step, run the verification and tick
`PROGRESS.md` before moving on.** If a step is interrupted, resume from the
first unticked item — do not restart.

**Step 0 — Scaffold.**
- Init git, `.gitignore` (ignore `~/.wayground/`-equivalent, `wqu-logs/`,
  `snaps/`, `*.har`, `__pycache__`, `.venv`, `*.xlsx` test artifacts except
  committed fixtures).
- `pyproject.toml` with `[project.scripts] wqu = "wqu.cli:main"`,
  `requires-python = ">=3.11"`, deps: `playwright>=1.40`, `openpyxl>=3.1`,
  `httpx` (only if API path), `typer` or `argparse`, `rich`.
  Dev extras: `pytest`, `ruff`.
- `LICENSE` (MIT), `NOTICE`, empty `README.md` skeleton, `PROGRESS.md` from
  Part 2.4, `docs/` directory.
- CI workflow `.github/workflows/ci.yml`: `ruff check . && pytest`.
  **CI must not run integration tests.** Mark integration tests
  `@pytest.mark.integration` and register the marker in `pyproject.toml`.
- **Verify:** `pip install -e ".[dev]"` succeeds; `wqu --version` prints.

**Step 1 — HAR analysis.**
- Write `docs/api-notes.md` documenting the chosen path (A / B / hybrid).
- **Verify:** the doc exists and names the endpoints (or explicitly says
  "DOM path — no clean endpoints").

**Step 2 — Vendor `wayground.py`.**
- Copy into `wqu/vendor/wayground_upstream.py`. MIT header intact.
- **Verify:** `python -c "import wqu.vendor.wayground_upstream"` succeeds.

**Step 3 — Session commands.**
- `wqu login` → launches browser, waits for user, saves `storage_state` to
  `~/.wayground/state.json`.
- `wqu check` → reloads state, does a lightweight probe, prints `[OK]` or
  `[EXPIRED]`.
- `wqu doctor` → all Part 4 checks.
- **Verify:** run `login` manually, then `check` → `[OK]`. Run `doctor` → all
  green.

**Step 4 — Dry-run.**
- `wqu publish <xlsx> --dry-run` → validates sheet name, columns, at least one
  question, `Correct` values parse to indices within bounds. Prints a plan
  table. No browser.
- **Verify:** on `tests/fixtures/sample_single.xlsx`, prints a green plan. On a
  deliberately broken fixture, prints actionable errors and exits 1.

**Step 5 — Publish (no assign).**
- Implement the create → upload → import-preview → publish flow.
- Banner handling: wait for `Looks good!` OR `Invalid correct option`. On error,
  scrape row numbers, save screenshot to `./wqu-logs/<ts>-<file>.png`, skip
  that file (or stop with `--fail-fast`). Never click Import unless
  `--force-import`.
- **Verify:** `wqu publish tests/fixtures/sample_single.xlsx --no-headless
  --no-assign` publishes a 2-question quiz, prints quiz URL.

**Step 6 — Assign.**
- Implement No deadline → Save and close → Select a class → Naic → Next →
  assignment name → uncheck accommodations → Next → Assign.
- Handle both "Select a class" and "Modify" button states (pre-selected class
  from previous run).
- **Verify:** `wqu publish tests/fixtures/sample_single.xlsx --no-headless
  --assign Naic` produces an assigned quiz. Manually confirm on Wayground.

**Step 7 — Join-link extraction.**
- Try DOM first (find `<input>` whose value matches
  `r'https?://wayground\.com/join\?gc=\d+'`). Fallback to clipboard read with
  `context.grant_permissions(['clipboard-read', 'clipboard-write'])` then
  `page.evaluate("navigator.clipboard.readText()")`.
- **Verify:** link matches the shape; opening it in a browser lands on the
  Wayground join page.

**Step 8 — End-to-end with `--assign`.**
- **Verify:** acceptance criteria from Part 12 pass for one file.

**Step 9 — Split-file handling.**
- Regex `r'^(?P<base>.+?)_pt(?P<n>\d+)\.xlsx$'` (case-insensitive).
- Natural-sort by `<n>`. Output labels `_Pt<N>` (capital P).
- **Verify:** `wqu publish DataComs_Quiz2_pt1.xlsx DataComs_Quiz2_pt2.xlsx
  --json` returns two entries with `_Pt1` and `_Pt2` labels, joined to their
  respective join links.

**Step 10 — Tests + CI.**
- Unit tests cover: `output.py` formatting, `xlsx` validation, natural sort,
  split-label normalization, **and the GUI-ready acid test from Part 2.6
  rule 6** (call `publish_one` from a REPL-style test with a mocked browser).
- Integration tests (marked `integration`) exercise publish/assign; opt-in via
  `WQU_INTEGRATION=1` + saved session.
- **Verify:** `pytest -m "not integration"` passes locally and in CI.

**Step 11 — Documentation.**
- README, ARCHITECTURE, `docs/selectors.md` (every selector + last verified
  date), `docs/troubleshooting.md`.
- **Follow Part 2.3 chunking rules.** README in ≥4 edits.
- **Verify:** fresh clone → follow README quickstart → works.

**Step 12 — Tag v0.1.0.**
- **Verify:** acceptance criteria in Part 12 all checked.

---

## Part 8 — Error handling & logging

- **Session expiry mid-flow:** detect redirect to login page or 401/403 →
  abort with `Run 'wqu login' again.` and exit 2.
- **Import preview errors:** print row numbers + banner text; save screenshot;
  default = skip that file and continue to next. `--fail-fast` = stop everything.
- **Publish-modal validation:** if the modal's Publish button is disabled, scrape
  required-field errors and print them.
- **Class not found:** if `Naic` is not in the class list, list available
  classes and exit 2.
- **Join link not found:** never fabricate. Print quiz URL + warning; exit 1.
- **Per-file summary:** always print a final summary after processing all files:
  ```
  Processed 3 files: 2 success, 1 failure
    OK   DataComs_Mod14  https://wayground.com/join?gc=...
    OK   DataComs_Mod15  https://wayground.com/join?gc=...
    FAIL DataComs_Mod16  Import preview errors (rows 12, 34) — screenshot wqu-logs/20261108T...png
  ```
  Exit code 0 only if all succeeded; else 1.
- **Logging:** every run writes `./wqu-logs/<ISO-ts>-<command>.log`. Rotate to
  keep max 50 files.
- **Trace on failure:** `wqu-logs/<ISO-ts>.trace.zip` per Part 5.
- **All error/warning/progress messages go to stderr** (Part 2.6 rule 1). Only
  the final `Label: URL` block goes to stdout.

---

## Part 9 — Idempotency & resume

- Before creating, list existing quizzes with the same title. If one exists:
  - Interactive: `[r]esume / [s]kip / [n]ew? [r]:`
  - `--yes`: default `new` but warn loudly.
  - `--force`: skip the check.
- Track created quiz IDs in `~/.wayground/created.json` so a later run can
  resume drafts.

---

## Part 10 — Testing & CI

- Unit tests (no browser): output formatting, xlsx validation, natural sort,
  split-label normalization, dry-run plan output, **GUI-ready acid test**
  (Part 2.6 rule 6).
- Integration tests (opt-in, `@pytest.mark.integration`): the full publish +
  assign flow on a 2-question fixture. Requires a real session and a Wayground
  account.
- CI runs only unit tests. `ruff check . && pytest -m "not integration"`.
- Provide `tests/fixtures/generate_fixtures.py` (openpyxl script) so fixtures
  are reproducible rather than opaque binaries.

---

## Part 11 — README requirements

The README must include:
- One-sentence pitch.
- Badges: license, CI, Python 3.11+.
- **GIF or short video** of a full run (`--no-headless`). If you cannot produce
  one, add `docs/demo.gif` as a TODO with a placeholder path.
- Install (Windows PowerShell): `pip install -e .` then
  `playwright install chromium`.
- Quickstart: three commands (`login`, `check`, `publish`).
- **"Relationship to the Quiz Toolkit"** section: link to the user's existing
  PowerShell repo. Explain that `Quiz Toolkit` produces `.xlsx` and this tool
  publishes them. Show `wqu publish "Quizzes\DataComs\_QuizizzOutput" --assign Naic`
  as the canonical one-liner.
- **"Known limitations"**: session expiry, Wayground UI/API changes can break
  the tool, MIT-licensed derivative of littleyi22's work.
- **"Security notes"**: session cookies stored in `~/.wayground/state.json`
  (chmod 600 on Unix; Windows ACL inherit) and never transmitted anywhere but
  `wayground.com`. Do not log cookie values.
- **"Library usage"** section (this is what makes Part 2.6 pay off): a short
  code block showing `publish_one()` called from Python, with a note that a
  GUI can wrap this. Do not build a GUI, just document the entry point.

---

## Part 12 — Acceptance criteria (v0.1.0 done)

- [ ] `wqu doctor` reports all green.
- [ ] `wqu login` then `wqu check` prints `[OK]`.
- [ ] `wqu publish <sample.xlsx> --no-headless --no-assign` publishes and prints
      the quiz URL.
- [ ] `wqu publish <sample.xlsx> --no-headless --assign Naic` publishes, assigns,
      and prints `Label: https://wayground.com/join?gc=...`.
- [ ] `wqu publish "folder" --assign Naic --json` emits a JSON array with one
      object per file, each with `join_link`.
- [ ] `wqu publish <broken.xlsx>` detects the red banner, prints row numbers,
      saves a screenshot, skips the file, exits 1 with the summary.
- [ ] `wqu publish DataComs_Quiz2_pt1.xlsx DataComs_Quiz2_pt2.xlsx` prints:
      ```
      DataComs_Quiz2_Pt1: https://...
      DataComs_Quiz2_Pt2: https://...
      ```
- [ ] `wqu publish --dry-run` touches no browser and prints a green plan.
- [ ] `wqu publish <folder>` processes files in natural order (`_pt1, _pt2,
      ..., _pt10`), not alphabetic.
- [ ] **GUI-ready acid test passes:** `python -c "from wqu.core import
      publish_one, PipelineOptions"` succeeds with no CLI, no globals, no
      printing at import time.
- [ ] `pytest -m "not integration"` passes; CI is green.
- [ ] README quickstart works verbatim on a fresh Windows machine.
- [ ] `PROGRESS.md` shows all boxes ticked.
- [ ] Repo pinned with description and topics: `quizizz`, `wayground`,
      `playwright`, `automation`, `edtech`, `python-cli`.

---

## Part 13 — Explicitly out of scope

- Modifying the user's PowerShell toolkit. Do not clone or read it either.
- Building a GUI. (Enforce Part 2.6; do not build the GUI.)
- PDF / screenshot / OCR input.
- Creating questions with AI.
- Multi-user, multi-tenant, cloud deployment.
- Any reverse-engineering beyond what the HAR naturally shows. **Do not**
  brute-force undocumented endpoints.
- A delete-quiz command (user deletes test quizzes manually via UI).

---

## Part 14 — When stuck, stop and ask

If any of the following is unclear, stop and list the question rather than
guessing:

- A selector cannot be located from the HAR, the discover command, or the
  user's screenshots.
- An API endpoint exists but its payload shape is not obvious from the HAR.
- The "No deadline" toggle's role is unclear (checkbox vs. switch vs. div).
- "Select a class" is sometimes absent (pre-selected state) and the detection
  rule is unclear.
- "Start now" (on the quiz page) may or may not yield a join link for
  unassigned quizzes — verify empirically before implementing the `--no-assign`
  path; if unverifiable, print `(NO JOIN LINK)` rather than guessing.
- The default state of "Set up accommodations" (screenshots show checked, but
  confirm on a live run before implementing the uncheck).

Guessing here produces a tool that silently corrupts the user's account. A
question costs 5 minutes; a wrong assumption costs hours of debugging and
manual cleanup of broken quizzes.

---

## Part 15 — Restating the critical rules

1. **Chunk every long write.** No Write/Edit call over ~150 lines. Split
   README, ARCHITECTURE, docs, and any source file > 200 lines into ≥3
   sequential edits. After each successful write, append to `PROGRESS.md`.
2. **HAR first, code second.** Do not write browser automation until you have
   captured and analyzed the HAR. The HAR decides the architecture.
3. **GUI-ready core, no GUI.** Core functions return data and accept
   `on_progress` / `should_cancel` callbacks. No `print()` inside core modules.
   No module-level mutable state. No `async`. Only `cli.py` prints and prompts.
   This is a 30-minute discipline that saves a full rewrite later. Do not build
   the GUI now.
4. **Do not read the user's PowerShell repo.** Use the redacted sample xlsx and
   the 20-line format excerpt in Part 1.5.