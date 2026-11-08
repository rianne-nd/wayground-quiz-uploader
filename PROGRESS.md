# Build progress

Resume rule: after an interruption, read this file first and continue from the
first unticked item. Do not restart from scratch.

## Checklist

- [x] 0. Scaffold repo, pyproject, CI, LICENSE, NOTICE, .gitignore — verified: `pip install -e ".[dev]"` OK, `wqu --version` → 0.1.0
- [x] 1. HAR capture → docs/api-notes.md — no HAR available; user chose DOM path; decision documented in docs/api-notes.md
- [x] 2. Vendor wayground.py — verified: `python -c "import wqu.vendor.wayground_upstream"` OK (done early; does not depend on HAR)
- [x] 3. wqu login / wqu check / wqu doctor — doctor all green; check → [OK] live; login() probe path ok=True
- [x] 4. wqu publish --dry-run — sample_single green exit 0; sample_broken error (row 3) exit 1
- [x] 5. wqu publish end-to-end (create + publish, no assign) — LIVE OK (headless), quiz 6aef80c1b5860fbbdcffa5ff
- [x] 6. wqu assign (No deadline → Naic → name → uncheck → Assign) — LIVE OK incl. glitch recovery + stacked modals
- [x] 7. Join-link extraction (DOM + clipboard fallback) — LIVE OK: clipboard gc=02211091 via Copy Join link
- [x] 8. wqu publish --assign end-to-end — LIVE OK: exit 0, `WQU_TEST_DELETE_ME: <join link>` printed
- [x] 9. Split-file output formatting + natural sort — LIVE: pt2 OK (gc=35896595); pt1 hit a transient publish stall, re-run resumed the draft → OK (gc=47955219)
- [x] 10. Tests + CI green — 61 unit tests pass, ruff clean; CI workflow pushed (first cloud run: check Actions tab)
- [x] 11. README + ARCHITECTURE + docs — README (4 chunks), ARCHITECTURE, docs/{selectors,troubleshooting,api-notes}.md
- [x] 12. Tag v0.1.0 — annotated tag pushed; remaining manual items for the user are listed in MORNING.md

## Log

- 2026-11-08: PROGRESS.md created (very first step per prompt Part 2.4).
- 2026-11-08: Step 0 scaffold done. pyproject (setuptools, argparse+rich CLI, pytest marker
  `integration`), .gitignore, LICENSE, NOTICE, CI workflow, minimal `wqu.cli` with `--version`.
  Playwright 1.64.1 / pytest 9.2 / ruff 0.17 installed via `pip install -e ".[dev]"`.
- 2026-11-08: Step 2 vendored `wqu/vendor/wayground_upstream.py` from littleyi22/wayground-quiz-creator-skill
  (verbatim; upstream file carries no inline MIT header — attribution in NOTICE; upstream LICENSE recorded).
  Note: upstream contains useful `data-testid` selectors for Wayground's editor DOM.
- 2026-11-08: User provided 22 workflow screenshots (`screenshots/step_01..22.png`) + `screenshots/screenshot-guide.md`.
  Analyzed all images; selector strategy updated with verified visual evidence:
  * create page marker "How would you like to get started?", tile "Start from scratch"
  * editor: "Import existing files" → "Spreadsheet"; modal "Import questions from spreadsheet"
  * banners "Looks good!" / "Invalid correct option" + "Next error"; error rows highlighted w/ row number column
  * publish modal: Name placeholder "Untitled Quiz" 0/64, Subject/Grade "Select..." triggers, options
    "Information Technology (IT)" / long Grade list (scroll needed for "University"), footer Cancel/Publish
  * "Great work! Your quiz is saved..." modal with X; quiz page /activity/admin/quiz/{24-hex}
  * assignment page /activity/admin/quiz/homework/{24-hex}: "Not assigned to a class" + "Select a class",
    "Start – End time" + "Change"
  * "Assign to a class" modal: search, "Select all", Naic row "12 students · 12 completed sessions";
    footer "Set up Accommodations" auto-becomes CHECKED after selecting class → must uncheck; Next disabled until selection
  * "Name this assignment (Optional)" modal: title input placeholder "Name this assignment for your students",
    second "Set up Accommodations" checkbox → keep unchecked; Next
  * gameplay page /gameplay/admin/activity/classic/{24-hex}: "Copy Join link" button, join code shown "5790 8499"
  * accommodations glitch: full-page "Set up learning supports..." screen → back-arrow top-left recovery
  * premium popup "Unlock 45+ premium features" → X dismiss
  * NOTE: no screenshot for the "No deadline" modal; implement from text spec with state verification.
- 2026-11-08: Guide updated to Step_00..Step_25 + docs order change (deadline AFTER class+name). Live
  selector-probe sessions produced these verified facts (see docs/selectors.md):
  * top-right Publish button aria-label is "Publish quiz" (not "Publish"); modal X is aria "xmark"
  * Subject trigger: data-testid=quiz-subject-select-box, text "Select relevant subject"; options are
    LI[role=option] under UL[role=listbox]; Grade: data-testid=publish-modal-grade-input-select-box
  * clicking Publish on an already-published unchanged quiz SKIPS the modal and navigates to the quiz page
  * "Import existing files" lazily renders + disappears once the quiz has questions → resumed drafts with
    questions skip import (prevents duplicates)
  * class modal: data-testid=class-assignment-with-accomodations-modal (sic); Naic row via text click;
    accommodations checkbox = assignment-setup-accommodations-toggle (CHECKED once a class is picked,
    covered by styled wrapper → JS click needed); final Assign = rightmost game-setting-continue-btn;
    Change = toggle-calendar-button; Select a class = select-class-button
  * No deadline = input[role=switch][data-testid=date-time-selector-input]; Save = timeline-selector-continue-button
  * accommodations glitch can strike right after quiz-page Assign as well as at Select-a-class; recovered
    via chevron-left back arrow; modals STACK (previous modal stays mounted — never wait for it to hide)
  * "Got it" coachmark intercepts clicks and must be dismissed
- 2026-11-09 (v0.1.3): Live step-tree UI (`wqu/ui.py`):
  * `LiveTreeUI` renders a continuously-updated rich.live.Live region on stderr: overall files bar +
    per-file panel `[i/n] name` with a step catalog (~33 expected steps) matched against on_progress
    messages; ✓ green done, ⠸ cyan active (animated, perf_counter timer), – skipped, - pending,
    ✗ red failed step with the error line inline; tree resets per file.
  * Prompts ([r]esume/assign/archive) now pause()/resume() the Live region (fixes the mangled
    prompt from the v0.1.2 screenshot); interactive mode keeps the tree instead of disabling it.
  * Result panel switched to box=HORIZONTALS (double-click selection no longer grabs │ characters);
    summary table gained a Time column; non-TTY runs fall back to plain styled lines (still timed).
  * publish_many gained an `on_result` callback (per-file completion signal; GUI-friendly);
    create_quiz emits "Navigating to the assessment page...". Tests: 93 passing.
- 2026-11-09 (v0.1.2): UX/speed/library upgrades:
  * CLI redesign: blue rich banner with version; live progress line (spinner + bar + [i/n] + current step)
    with per-step colours (cyan/green/yellow/red) and spacing between files; rich summary table
    (File/Status/Join link) + caption; clipboard copy still happens before the results panel;
    all presentations go to stderr, stdout stays the result block/JSON.
  * Speed: coachmark checks are instant `is_visible()` probes (single 400ms grace on landing);
    accommodations-page probing is instant with bounded ~1.2s re-checks (no 10s hidden-wait);
    public assignment goes straight to the final Assign.
  * Library manager (`wqu/library.py` + `wqu library` command): pre-flight cap check on
    `my-library/createdByMe` ("X / 20" counter), row scraping (title/age/checkbox), interactive
    archive chooser (rich table + prompts; `#,#` | `o<N>` | `a` | Enter), `--auto-archive-oldest N`,
    bulk Archive + confirmation modal (`archive-activity-modal-primary-button`), rescan + limit gate.
  * Live-verified library flow: archived our own test quiz WQU_TEST_DELETE_ME → counter 11 → 10.
  * Tests: 79 passing; LICENSE/NOTICE/pyproject now copyright Edrianne John Puno; version 0.1.2.
- 2026-11-09: Post-review fixes from the user's headed live run (all LIVE-verified in one final run):
  1. publish modal: dismiss the "Great work!" popup FIRST (still on /edit), then wait for the URL
     to drop /edit (20s, with direct navigation fallback).
  2. coachmark: on the quiz page, dismiss the "Got it" tip immediately (single 1.5s window), then Assign.
  3. public assignment: `--no-assign` / answering N now runs the PUBLIC flow (no class, no deadline
     change) straight to the final Assign — a real join link IS produced; `--keep-draft` remains the
     only no-assignment mode.
  4. clipboard: native `clip.exe` (UTF-16LE, then text=True) replaces tkinter-first; verified via
     Get-Clipboard.
  5. CLI: rich Panel around the result block (green/red), spacing, clipboard copied before printing.
  Live result: DataComs_Mod14 (quiz 6af01babaf64e5b6c4061644) → public assignment →
  https://wayground.com/join?gc=57828115&source=liveDashboard; exit 0. Tests: 70 passing.
- 2026-11-08: LIVE end-to-end (headless) succeeded for a single file with --assign Naic:
  resume → skip import → publish-skip → glitch recovery → Naic → uncheck accommodations ×2 → name →
  No deadline ON → final Assign → gameplay → Copy Join link → clipboard → formatted stdout, exit 0.
  Test entity created for later cleanup: quiz "WQU_TEST_DELETE_ME" (id 6aef80c1b5860fbbdcffa5ff).
