# Session report — build complete, v0.1.0 (2026-11-08)

The tool is built and the primary flow was **live-verified headlessly against
your account**. Everything below is either done, or a short manual action
that only you can do.

## 1. Clean up the test artifacts I created on Wayground

Three test quizzes were published (and assigned to **Naic** with No deadline).
Delete them in the Wayground UI when convenient:

| Quiz | Quiz page | Join link |
|---|---|---|
| `WQU_TEST_DELETE_ME` | https://wayground.com/activity/admin/quiz/6aef80c1b5860fbbdcffa5ff | https://wayground.com/join?gc=02211091&source=liveDashboard |
| `DataComs_Quiz2_Pt1` | https://wayground.com/activity/admin/quiz/6aef963cb42346ba30d40013 | https://wayground.com/join?gc=47955219&source=liveDashboard |
| `DataComs_Quiz2_Pt2` | https://wayground.com/activity/admin/quiz/6aef9a4ed116dfce6d4986c8 | https://wayground.com/join?gc=35896595&source=liveDashboard |
| `DataComs_Mod14` (public, 1-day) | https://wayground.com/activity/admin/quiz/6af01babaf64e5b6c4061644 | https://wayground.com/join?gc=57828115&source=liveDashboard |

> **Update 2026-11-08 (v0.1.1):** implemented and live-verified five fixes from your
> headed run — "Great work!" dismissal order, fast "Got it" handling, the
> public (no-class) assignment flow for `--no-assign`, native `clip.exe`
> clipboard (verified with `Get-Clipboard`), and the rich Panel result box.
> The `DataComs_Mod14` row above was created by that verification run. See
> `PROGRESS.md` → Log for details.
>
> **Update 2026-11-08 (v0.1.3):** live step-tree UI (✓/⠸/–/-/✗ per step with
> per-step timings, overall files bar, error line inside the tree), prompts
> pause/resume the Live region (fixes the mangled `[r]esume` prompt),
> result block switched to borderless `HORIZONTALS`, summary table gained a
> **Time** column. Live-verified: full run in 36.8s, exit 0, join link
> `gc=24392467`; the `WQU_V013_TEST` quiz it created was archived through
> the library module (counter back to ~10/20). 93 unit tests pass.
>
> **Update 2026-11-08 (v0.1.3 hotfix):** the first headed run exposed a Rich 14
> integration crash in the live tree — `NotRenderableError` when the library
> pre-check printed above the active live region (a bound method had been passed
> as the renderable). Fixed with `Live(get_renderable=...)`; regression test
> added. 94 unit tests pass.
>
> **Update 2026-11-14 (v0.1.6):** dynamic classes — the assign flow now
> scrapes your actual classes from the picker; a missing/absent class offers
> *pick existing / create new / public* (or `--create-class` for hands-off).
> Class creation runs on `/manage/admin/classes` with the optional guardian
> email checkbox. **Your session expired on 2026-11-13** — run `wqu login`,
> then live-verify: `wqu publish tests\fixtures\sample_single.xlsx --assign "NoSuchClass"`
> (expect the select/create/public prompt), `--assign NewClass --create-class`
> (expect auto-creation + assignment), and delete any test class afterwards on
> the classes page.
>
> **Update 2026-11-10 (v0.1.4):** login pre-flight. `wqu publish` now probes
> the stored session before the library check and before creating anything; an
> expired/missing session used to slip past (the library pre-check swallowed
> `SessionExpiredError`) and the run burned ~40 s in the create/import flow.
> Sign-in detection is eager: a `/login` bounce or a "Log in / Sign in" CTA
> means signed out, and a protected admin URL (`/activity/`, `/explore/`) with
> no such CTA counts as signed in after ~1.5 s — the old probe waited up to
> 30 s for the exact "How would you like to get started?" heading, which made
> a successful sign-in look like a hang. The state is now saved immediately
> after you press Enter, before verification, so a slow probe can no longer
> lose your login. In an interactive terminal the CLI says you're signed out,
> **opens a browser for you to sign in**, and retries the same batch
> automatically; `--yes` / `--quiet` / `--json` exit 2 with the message
> instead. 109 unit tests pass (`tests/test_login_preflight.py`).
>
> **Update 2026-11-08 (v0.1.3 UI follow-up):** completed file boxes now freeze
> as full reports — all 33 steps with per-step timings — instead of collapsing
> to "... (N earlier steps)". In-progress files keep the compact window; batch
> runs leave one full panel per file. 96 unit tests pass.
>
> **Update 2026-11-08 (v0.1.2):** CLI redesign (banner, live progress line,
> coloured steps, summary table), speed-ups (instant coachmark/glitch checks,
> immediate public Assign), and the library manager (`wqu library`,
> pre-flight 20-quiz cap check, interactive archiving,
> `--auto-archive-oldest N`). Live-verified: pre-flight reported 10/20, a
> fresh `WQU_V012_TEST` quiz was published + publicly assigned + joined
> (`gc=52933409`), then archived through `library.archive_items`
> (11/20 → 10/20). **`WQU_TEST_DELETE_ME` and `WQU_V012_TEST` are now in the
> Archived tab** (recoverable on the free plan; delete permanently from
> there if you like). Free-tier counter is back to 10/20.

Deleting the quiz removes its assignment. `~/.wayground/created.json` is the
same list (machine-readable). There are no other side effects.

## 2. GitHub — two small settings only you can change

The code is pushed to `main` at
https://github.com/rianne-nd/wayground-quiz-uploader (I pushed on your behalf
using the remote you created). Because `gh` was not available:

1. **Settings → Branches**: set the default branch to `main`, then delete the
   leftover `master` branch (the first push created it; GitHub refused the
   remote delete because it was the default at that moment).
2. **Repo home → ⚙ (About)**: set the description (suggested: *"Publish Quiz
   Toolkit .xlsx quizzes to Wayground (formerly Quizizz): upload, publish,
   assign, and return join links — Playwright + Python"*) and topics:
   `quizizz`, `wayground`, `playwright`, `automation`, `edtech`, `python-cli`.
3. Actions tab: the CI workflow ran on push — confirm both jobs are green
   (it runs `ruff check .` + `pytest -m "not integration"` on 3.11/3.12).

## 3. Optional spot-checks (things I deliberately did not do)

- **Cold login**: I verified `wqu check` → `[OK]` live, and the `login()`
  probe path by saving the existing session. Your very first `wqu login` on a
  fresh machine needs a human sign-in — try it when convenient.
- **`--no-headless`**: all live runs were headless. The flag only flips
  `headless=False`; run `wqu publish tests\fixtures\sample_single.xlsx
  --no-headless --no-assign` once to see the browser dance (creates another
  test quiz — delete afterwards).
- **Red-banner live path**: the broken fixture is caught by *local*
  validation (correct answer 9 → out of range), so the live red-banner path
  (detect → scrape rows → screenshot → Cancel → skip) is covered by unit
  tests + your step_04 screenshot, not by a live run. If you have a
  toolkit-made file that triggers "Invalid correct option" online, run it
  once and share the error text/screenshot.
- **`docs/demo.gif`**: placeholder (TODO) in the README; record a
  `--no-headless` session and drop the file in `docs/`.

## 4. What was verified live (headless, real account)

- `wqu doctor` → all green; `wqu check` → `[OK]`.
- `wqu publish … --dry-run` → green plan / exit 0; broken file → row-level
  error / exit 1.
- Full publish: create → "Start from scratch" → Spreadsheet import → green
  banner → Import → publish modal (Subject/Grade) → quiz page.
- Full assign: Assign → *accommodations glitch* detected & recovered → Naic →
  "Set up Accommodations" force-unchecked (both modals) → name → **No
  deadline** (state-read before clicking) → final Assign → gameplay → "Copy
  Join link" → clipboard capture → `Label: URL` on stdout.
- Split pair (2 files, one session): natural order, `DataComs_Quiz2_Pt1/_Pt2`
  labels, per-file join links; a transient publish stall on pt1 was recovered
  by simply re-running (resume found the draft and completed it).
- 61 unit tests + ruff clean locally (`pytest -m "not integration"`).

## 5. Ready-made commands for your first real batch

```powershell
wqu check
wqu publish "Quizzes\DataComs\_QuizizzOutput" --dry-run        # look first
wqu publish "Quizzes\DataComs\_QuizizzOutput" --assign Naic    # the real run
```

Interactive (asks per file, split parts default N):

```powershell
wqu publish "Quizzes\DataComs\_QuizizzOutput"
```

When something inevitably drifts in Wayground's UI: the run leaves a
screenshot + `trace.playwright.dev` trace in `wqu-logs\`; re-map with
`wqu discover <url>`; the selector inventory with statuses lives in
`docs/selectors.md`.
