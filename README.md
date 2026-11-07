# wayground-quiz-uploader (`wqu`)

**One command to take the `.xlsx` quizzes your Quiz Toolkit produces and publish
them to Wayground (formerly Quizizz) — with an optional class assignment and a
ready-to-paste join link.**

Status: under construction (v0.1.0). Full documentation lands with the release.

## Install (Windows PowerShell)

```powershell
pip install -e .
playwright install chromium
```

## Quickstart

```powershell
wqu login    # once: sign in to Wayground in the opened browser
wqu check    # verify the saved session still works
wqu publish "Quizzes\DataComs\_QuizizzOutput" --assign Naic
```

See `PROGRESS.md` for the build checklist and `docs/` for architecture notes.

## License

MIT — see `LICENSE`. Vendored components and attribution: see `NOTICE`.
