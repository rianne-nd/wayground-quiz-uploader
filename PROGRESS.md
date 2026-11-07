# Build progress

Resume rule: after an interruption, read this file first and continue from the
first unticked item. Do not restart from scratch.

## Checklist

- [x] 0. Scaffold repo, pyproject, CI, LICENSE, NOTICE, .gitignore — verified: `pip install -e ".[dev]"` OK, `wqu --version` → 0.1.0
- [ ] 1. HAR capture → docs/api-notes.md
- [x] 2. Vendor wayground.py — verified: `python -c "import wqu.vendor.wayground_upstream"` OK (done early; does not depend on HAR)
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

## Log

- 2026-11-08: PROGRESS.md created (very first step per prompt Part 2.4).
- 2026-11-08: Step 0 scaffold done. pyproject (setuptools, argparse+rich CLI, pytest marker
  `integration`), .gitignore, LICENSE, NOTICE, CI workflow, minimal `wqu.cli` with `--version`.
  Playwright 1.64.1 / pytest 9.2 / ruff 0.17 installed via `pip install -e ".[dev]"`.
- 2026-11-08: Step 2 vendored `wqu/vendor/wayground_upstream.py` from littleyi22/wayground-quiz-creator-skill
  (verbatim; upstream file carries no inline MIT header — attribution in NOTICE; upstream LICENSE recorded).
  Note: upstream contains useful `data-testid` selectors for Wayground's editor DOM.
