# Troubleshooting

## Session expired / "run `wqu login` again"

Symptoms: `wqu check` prints `[EXPIRED]`; a run exits **2** with "Session
expired mid-flow" or "No saved session at C:\Users\…\.wayground\state.json".

Fix:

```powershell
wqu login
wqu check    # expect: [OK] Session is valid.
```

Runs abort (rather than limping on) when the session dies mid-file. A run
that dies this way stops cleanly; already-published quizzes are recorded in
`~/.wayground/created.json`.

## Red banner: "Invalid correct option"

What happens: the tool waits for the preview banner; on red it scrapes the
highlighted row numbers (best effort — the DOM does not always expose them),
continues clicking nothing dangerous, saves a screenshot, and by default
**skips this file and continues** with the next one. `--fail-fast` stops
everything instead.

- Screenshot: `wqu-logs\<timestamp>-<file>.png`
- The file is *not* imported unless you pass `--force-import` (loudly warned).
- Fix the workbook (check the `Correct Answer` column uses 1-based option
  indices that point at non-empty Option cells), then re-run.

## Publish modal errors

If the modal's Publish button is disabled, the tool scrapes the red hints
("Name should be at least 4 characters long", "Please select the subject",
"Please select the grade") and fails that file with them in the message.
Usually this means a title below 4 characters — pass a longer `--title`.

## The modal button spun forever and the file failed

Observed once (live, 2026-11-08): the modal Publish button entered its
loading state but no publish request ever reached Wayground (transient
network/server stall). The tool fails that file with a clear message and
**the quiz remains a draft**. Simply re-run the same command: the
duplicate-title check finds the draft in `created.json` and resumes it
(skips re-importing questions, then publishes and assigns normally). This
recovery path is live-verified.

## "Resuming … skipping the import" — I wanted new questions imported

Resume is duplicate-safe on purpose: if the draft already has questions, the
import step is skipped because re-importing would duplicate them. To force a
fresh import, delete the old questions in the Wayground editor first (or
create a new quiz with `--force` after deleting the draft).

## The full-page "Set up Accommodations" screen appeared

That is Wayground's own quirk (the "accommodations glitch"), not a bug — it
can appear right after clicking **Assign** or **Select a class** on a fresh
browser context. The tool detects it and clicks the back arrow
(`chevron-left`) to return, then continues. If a run ever fails with
"accommodations page still visible after recovery attempts", run
`wqu discover <the url from the log>` and check
[docs/selectors.md](selectors.md) → the back-arrow selector may need updating.

## Something timed out mid-flow

Every failure leaves:

- a full-page screenshot: `wqu-logs\<ts>-<file>.png`
- (first failure of a run) a Playwright trace: `wqu-logs\<ts>.trace.zip`

Open the trace at <https://trace.playwright.dev> (drag the zip in). You get
the DOM snapshots, network log, and console for every step — usually enough
to see exactly which element changed. Also check the run log:
`wqu-logs\<ts>-publish.log`.

Then, to re-map the UI:

```powershell
wqu discover https://wayground.com/activity/admin/assessment
```

Interact until the screen in question is visible, press Ctrl+C, and inspect
`./snaps/<ts>-aria.txt`, `-page.html`, `-shot.png`. Update
`wqu/selectors.py` (keep the `status`/`evidence` fields honest) and
`docs/selectors.md`.

## `wqu doctor` failures

| Check | Fix |
|---|---|
| Chromium installed | `python -m playwright install chromium` |
| playwright importable | `pip install -e .` (or `pip install playwright`) |
| stored session (missing/stale) | `wqu login` |
| wayground.com reachable | check your connection / VPN |
| wqu-logs writable | pick another dir: `--logs-dir <path>` |

## Class not found

If the requested class (default interactive answer: `Naic`) is not in the
class modal, the run exits **2** and lists the available class names.
Use `--assign "<exact name>"`.

## "NO JOIN LINK — published but not assigned"

This marker now appears only with `--keep-draft` (publish without assigning
anything) — or when a link genuinely could not be captured, in which case that
file is reported as a failure (exit 1) with a screenshot.

`--no-assign` and answering `N` at the prompt do **not** produce it: a quiz can
be assigned publicly without a class (platform default 1-day deadline), and
that path still yields a real join link — the tool runs the final Assign and
copies the link exactly like the class flow.

## The public assignment has a 1-day deadline — I wanted no deadline

Correct and by design: the deadline modal ("Change" → "No deadline") is part
of the class flow. The public flow never touches time settings and keeps the
platform default. If you need "No deadline" without students, use the class
flow (`--assign Naic`) — the quiz stays visible to you and the class can be
modified later in the UI.

## Cleaning up test quizzes

There is deliberately no delete command. Test quizzes/assignments are deleted
manually in the Wayground UI. `~/.wayground/created.json` lists every quiz
this tool created (title, id, url, status, join link) — use it as your
checklist.
