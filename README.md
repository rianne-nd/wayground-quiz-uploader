# wayground-quiz-uploader (`wqu`)

**Turn the `.xlsx` quiz workbooks your Quiz Toolkit produces into published
Wayground (formerly Quizizz) quizzes — with an optional class assignment and a
ready-to-paste join link — from a single command.**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![CI](https://github.com/rianne-nd/wayground-quiz-uploader/actions/workflows/ci.yml/badge.svg)](https://github.com/rianne-nd/wayground-quiz-uploader/actions/workflows/ci.yml)
![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)

```text
PS> wqu publish "Quizzes\DataComs\_QuizizzOutput" --assign Naic
DataComs_Mod14: https://wayground.com/join?gc=24923411&source=liveDashboard
DataComs_Mod15: https://wayground.com/join?gc=24923488&source=liveDashboard
```

> **Demo:** `docs/demo.gif` — TODO (record with `--no-headless`; placeholder
> until the walkthrough video is captured).

- Uploads workbooks through the real import flow (spreadsheet preview +
  "Looks good!" check) and **refuses broken files** instead of publishing them.
- Publishes with your usual metadata (Subject *Information Technology (IT)*,
  Grade *University*) — configurable via flags.
- Optionally assigns to a class (e.g. `Naic`), sets **No deadline**, names the
  assignment, and **always unchecks "Set up Accommodations"**.
- Copies the formatted `QuizName: <join link>` block to your clipboard.
- Handles the realities of the live UI: the premium popup, the "Sessions is
  now Reports" tip, and the intermittent full-page *Set up Accommodations*
  redirect (it clicks the back arrow and carries on).

*The Windows walkthrough, command reference, and limitations continue below.*

## Install (Windows PowerShell)

```powershell
git clone https://github.com/rianne-nd/wayground-quiz-uploader
cd wayground-quiz-uploader
pip install -e .
python -m playwright install chromium
```

Requires Python 3.11+.

## Quickstart (three commands)

```powershell
wqu login    # opens a browser: sign in to Wayground, then press Enter here
wqu check    # verify: expect [OK] Session is valid.
wqu publish tests\fixtures\sample_single.xlsx --no-headless --no-assign
```

The canonical one-liner once you're set up:

```powershell
wqu publish "Quizzes\DataComs\_QuizizzOutput" --assign Naic
```

See what would happen without launching a browser:

```powershell
wqu publish "Quizzes\DataComs\_QuizizzOutput" --dry-run
```

## What a run does, step by step

0. **Pre-flight (free-tier cap):** checks your library counter
   (`X / 20` active quizzes). If this batch would exceed 20, it shows the
   quiz list with ages and lets you archive old ones (or use
   `--auto-archive-oldest N` for a hands-off run). Nothing is archived
   without your explicit choice.
1. Validates every workbook locally (sheet `Create a Quiz`, 11 columns,
   1-based `Correct Answer` indices).
2. Creates the quiz ("Start from scratch"), opens the spreadsheet import,
   waits for the preview banner, and clicks **Import** only on "Looks good!"
   (a red "Invalid correct option" banner aborts that file and saves a
   screenshot; `--force-import` overrides).
3. Fills the publish modal (Name / Subject / Grade) and publishes.
4. Asks per file whether to assign. Two paths:
   - **with a class** (`--assign Naic`, or answering `y`): tick the class,
     force-uncheck "Set up Accommodations", name the assignment, set
     **No deadline**, then click **Assign**;
   - **public** (answering `N`, or `--no-assign`): skip straight to the final
     **Assign** — the quiz is assigned publicly with the platform's default
     1-day deadline.

   (A quiz can be assigned publicly without a class — that is a supported
   Wayground path, not a skip.)
   While this runs, a **live step tree** shows exactly where the automation
   is (✓ done, ⠸ active with a running timer, `–` skipped, `✗` failed with
   the error inline), plus an overall files bar:

   ```text
   Overall: ████████░░░░░░░░░░░░░░░░ 1/3 files (24.5s elapsed)

   ┌─ [2/3] DataComs_Mod15.xlsx ────────────────────────────────────┐
   │  Steps: ████████████████████░░░░ 28/33 (5.2s elapsed)          │
   │  ├─ ✓ Preview validated (Looks good!)               [00.9s]    │
   │  ├─ ⠸ Clicking final Assign                        [ 3.2s...]  │
   │  └─ - Copying join link                           [ waiting ]  │
   └────────────────────────────────────────────────────────────────┘
   ```

   While a file is running the tree is windowed (recent steps plus a few
   upcoming ones). When the file finishes, its panel freezes with **every**
   step and timing so you can review the whole run; in a batch you get one
   full panel per file.

5. Clicks **Copy Join link** and prints a borderless result block (no `│`
   characters, so double-click selection copies cleanly) plus a summary
   table with per-file timings:

   ```text
   ─────────────────────────── wqu results ───────────────────────────
   QuizName: https://wayground.com/join?gc=24923411&source=liveDashboard
   ────────────────────────────────────────────────────────────────────
   File                 Status   Time      Join link / detail
   sample_single.xlsx   OK       52.1s     https://wayground.com/join?gc=…
   Processed 1 files: 1 success, 0 failure
   ```

   Split files (`*_pt1.xlsx`, `*_pt2.xlsx`, …) print `QuizName_Pt1:`, `_Pt2:`
   in natural order. The block is copied to the clipboard (native `clip.exe`)
   *before* the panel is printed. `--keep-draft` (publish, assign nothing) is
   the only mode that can yield no join link — then the quiz URL is printed
   with `(NO JOIN LINK — published but not assigned)`. Links are never
   fabricated.

## Commands

| Command | What it does |
|---|---|
| `wqu login` | Opens a browser for manual sign-in; saves the session to `~/.wayground/state.json`. |
| `wqu check` | Reloads the session and probes a protected page: `[OK]` or `[EXPIRED]`. |
| `wqu doctor` | Environment diagnostics: Python, Playwright + Chromium, session age, network, logs dir, openpyxl. |
| `wqu discover <url>` | Opens the URL headed; on Ctrl+C saves HTML/aria/screenshot to `./snaps/` — the canonical way to re-verify selectors. |
| `wqu library` | Opens your Wayground library headed so you can archive/manage quizzes (free accounts cap active quizzes at 20). |
| `wqu list` | Lists active quizzes (number, title, age, short id); `--json` emits `{total, limit, quizzes}` for scripting. |
| `wqu archive` | Archives quizzes: `--all`, `--test` (dev artifacts), `--id <id>...` (exact or prefix), or an interactive numbered table. |
| `wqu publish <path>...` | Files and/or folders; the full pipeline. |

### `wqu publish` flags

| Flag | Default | Meaning |
|---|---|---|
| `--subject "Information Technology (IT)"` | as shown | subject in the publish modal |
| `--grade University` | `University` | grade in the publish modal |
| `--title <name>` | derived from filename | quiz title (single file only); `Quizizz_` is stripped, `_pt<N>` → `_Pt<N>` |
| `--assign <class>` | — | force-assign to this class (skips the prompt) |
| `--no-assign` | — | skip the class picker; still assigns **publicly** (1-day deadline) so a join link is produced; suppresses the prompt |
| `--no-deadline` / `--deadline` | no deadline | `--deadline` reserved for future use (class flow only; the public flow always keeps the platform default) |
| `--no-headless` | off (headless) | show the browser window |
| `--dry-run` | off | validate only; no browser |
| `--json` | off | machine-readable array: `file`, `title`, `quiz_url`, `join_link`, `assigned`, `error` |
| `--quiet` | off | only the `Label: URL` lines on stdout |
| `--keep-draft` | off | publish and skip assignment **entirely** (no class, no public assignment; join link may be unavailable) |
| `--force` | off | skip duplicate-title detection |
| `--fail-fast` | off | stop on the first failed file (default: skip and continue) |
| `--force-import` | off | DANGEROUS: import despite preview errors |
| `--interactive-review` | off | pause after each publish, show the URL, Enter before assigning |
| `--yes` | off | non-interactive defaults for all prompts |
| `--from-toolkit-root <path>` | — | auto-discover `Quizzes\*\_QuizizzOutput\*.xlsx` under this root |
| `--auto-archive-oldest <N>` | — | when the 20-quiz library cap would be exceeded, archive the N oldest quizzes automatically (no prompt) |

Output rules: everything status-like goes to **stderr**; the final
`Label: URL` block (or `--json`) goes to **stdout** and is copied to your
clipboard in human mode only. Exit codes: `0` all good, `1` a file failed,
`2` configuration/session problem (e.g. run `wqu login` again).

### Managing the library from the CLI

```powershell
wqu list                       # index, title, age, short id + X/20 counter
wqu list --json                # machine-readable (future GUI / scripting)
wqu archive --test             # dev artifacts: title contains TEST / sample / WQU_V / DELETE_ME
wqu archive --id 6af0806f      # by id (exact, or a unique prefix); several ids allowed
wqu archive --all              # everything (asks for confirmation in a terminal)
wqu archive                    # no flags: interactive numbered table + prompt
```

Every archive prints `✓ Archived: <title>` per quiz and ends with
`Archived N quiz(zes). Library is now X/20.`

Troubleshooting and recovery (expired sessions, red banners, live-UI crashes,
where traces land): see [docs/troubleshooting.md](docs/troubleshooting.md).
Selector inventory: [docs/selectors.md](docs/selectors.md).

## Relationship to the Quiz Toolkit

The **Quiz Toolkit** (PowerShell, separate repo) converts AI-formatted text
into Quizizz-ready `.xlsx` workbooks. **This tool picks up where it ends:** it
consumes those `.xlsx` files and publishes them to Wayground. The two never
touch each other's files — the handoff is a folder of workbooks, which is
exactly what the canonical one-liner uses:

```powershell
wqu publish "Quizzes\DataComs\_QuizizzOutput" --assign Naic
```

…or, without copy-pasting paths, from the toolkit root:

```powershell
wqu publish --from-toolkit-root "C:\path\to\quiz-toolkit" --assign Naic
```

## Library usage (no CLI)

The automation core is a plain Python library — a future GUI is a thin wrapper
around the same calls:

```python
from pathlib import Path
from wqu.core import PipelineOptions, Session, publish_one

options = PipelineOptions(assign_class="Naic", headless=True)

def progress(message: str) -> None:
    print(message)  # a GUI would update a progress bar instead

result = publish_one(Path(r"Quizzes\DataComs\_QuizizzOutput\DataComs_Mod14.xlsx"),
                     options, on_progress=progress)
print(result.join_link)   # https://wayground.com/join?gc=...&source=liveDashboard
```

`publish_many(files, options, session=..., on_progress=..., should_cancel=...)`
adds batching with one browser session, a per-file assign decision callback,
and between-files cancellation — the pieces a GUI needs on day one.

## Known limitations

- **Session expiry:** Wayground sessions expire. `wqu publish` probes the
  login first; in an interactive terminal it tells you and opens a browser to
  sign in, then continues the batch. Non-interactive runs exit **2** with
  "run `wqu login` again".
- **UI drift:** this is DOM automation. Wayground ships UI changes; if a
  selector breaks you get a screenshot + Playwright trace in `wqu-logs/`.
  Re-verify with `wqu discover <url>` and update `wqu/selectors.py`
  (statuses are tracked in `docs/selectors.md`).
- **Resume re-publishes:** resuming an existing draft re-runs publish
  (metadata is re-applied). If the draft already has questions, the import is
  skipped to avoid duplicates; delete unwanted rows in the editor first.
- **Interactions taken from live probing are noted in ARCHITECTURE.md**
  (stacked modals, styled inputs, glitch recovery). They are defensive, but a
  Wayground redesign can still break them.
- MIT-licensed derivative work: portions originate from littleyi22's
  [wayground-quiz-creator-skill](https://github.com/littleyi22/wayground-quiz-creator-skill)
  (see `NOTICE`).

## Security notes

- Session cookies live in `~/.wayground/state.json` (chmod 600 on Unix;
  Windows ACL inheritance). They are sent only to `wayground.com` by the
  browser itself — `wqu` never transmits them anywhere else.
- Cookie values are never logged. `wqu-logs/*.log` contains progress and
  errors only.
- `docs/` HAR captures (if you ever make one) contain session cookies: keep
  them gitignored (they are), never commit them.

## Development

```powershell
pip install -e ".[dev]"
ruff check .
pytest -m "not integration"     # unit tests, no browser
$env:WQU_INTEGRATION="1"; pytest -m integration   # live, opt-in, needs wqu login
```

## License

MIT — see [LICENSE](LICENSE). Attribution for vendored code:
[NOTICE](NOTICE).
