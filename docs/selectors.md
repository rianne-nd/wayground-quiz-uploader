# Selector inventory

Every UI element the automation touches, with how it was verified. All
verification dates are **2026-11-08**. Statuses:

- **verified-live** — exercised against the real Wayground UI in a headless
  probe or a full run; exact attribute/test-id observed.
- **visual** — matches the user's screenshots (`screenshots/Step_*.png`) and
  the definitive walkthrough guide; DOM attributes not yet observed.
- **visual-partial** — seen in screenshots; the exact control needs an
  occasional live re-check (documented inline).

Registry: `wqu/selectors.py`. Never select by CSS class — Wayground uses
hashed class names. Modals are found via `find_dialog()` (role=dialog first,
then a `data-wqu-dialog` marker on the nearest fixed/absolute overlay).

## Create & publish

| Element | Selector | Status |
|---|---|---|
| Start from scratch tile | role button "Start from scratch" → text | visual |
| Assessment page marker | text "How would you like to get started?" | visual |
| "Import existing files" section (lazy-rendered) | text "Import existing files" (scroll to render) | visual |
| Spreadsheet button | role button "Spreadsheet" | visual |
| Import modal | role=dialog / marker containing "Import questions from spreadsheet" | visual |
| Upload from device | role button "Upload from device" | visual |
| File input | `input[type=file]` inside the modal (fallback: file chooser) | visual |
| Success banner | text "Looks good!" | visual |
| Error banner | text "Invalid correct option" (+ "Next error" button) | visual |
| Import confirm | role button "Import" (dialog-scoped) | visual |
| Cancel | role button "Cancel" (dialog-scoped) | visual |
| Editor ready marker | role button aria **"Publish quiz"** | **verified-live** |
| Publish button (top bar) | role button **"Publish quiz"** — aria-label overrides the visible "Publish" text | **verified-live** |
| Questions count chip | regex `\d+\s+Questions` | **verified-live** |
| Publish modal | dialog containing "Great, you're almost done" | **verified-live** |
| Name input | placeholder "Untitled Quiz" (maxlength=64) | **verified-live** |
| Subject dropdown | `[data-testid=quiz-subject-select-box]` (text "Select relevant subject") | **verified-live** |
| Grade dropdown | `[data-testid=publish-modal-grade-input-select-box]` ("Select relevant grade") | **verified-live** |
| Dropdown options | `UL[role=listbox] > LI[role=option]` — click by exact text | **verified-live** |
| Modal Publish / Cancel | role button "Publish" / "Cancel" (aria matches text) | **verified-live** |
| Modal close X | role button **"xmark"** (aria-label) | **verified-live** |
| Saved modal | text "Your quiz is saved" / "Great work!" | visual |

## Assign flow

| Element | Selector | Status |
|---|---|---|
| Quiz page Assign | role button **"Assign"** (aria-label; also a back "chevron-left" nearby) | **verified-live** |
| Quiz-page coachmark | role button "Got it" (intercepts clicks) | **verified-live** |
| Assignment Details marker | text "Assignment Details" | **verified-live** |
| Select a class | `[data-testid=select-class-button]` | **verified-live** |
| Modify (class already attached) | role button "Modify" | visual |
| Class row (Naic) | text "Naic" click (checkbox aria is "choose-class"; text click verified) | **verified-live** |
| Class selected hint | text "Pick specific students" (appears after ticking) | **verified-live** |
| Class modal | dialog `class-assignment-with-accomodations-modal` (sic) | **verified-live** |
| Set up Accommodations checkbox | `[data-testid=assignment-setup-accommodations-toggle]` — **CHECKED once a class is selected**; input is covered by a styled knob → click via `click_control` (JS fallback) | **verified-live** |
| Next (both modals) | role button "Next" | **verified-live** |
| Name assignment modal | text "Name this assignment (Optional)" | **verified-live** |
| Assignment title input | placeholder "Name this assignment for your students" | visual (modal reached; input not individually exercised) |
| Change (Start – End time) | `[data-testid=toggle-calendar-button]` | **verified-live** |
| No deadline toggle | `input[role=switch][data-testid=date-time-selector-input]`, aria-checked readable; styled knob → `click_control` | **verified-live** |
| Save and close | `[data-testid=timeline-selector-continue-button]` | **verified-live** |
| Deadline applied marker | text "No deadline" on the details row | **verified-live** |
| Final Assign | rightmost role button "Assign" (breadcrumb copy exists on the left) | **verified-live** |

## Join link & incidentals

| Element | Selector | Status |
|---|---|---|
| Copy Join link | role button "Copy Join link" | **verified-live** |
| Join link source | DOM scan (`input`/text) → clipboard read after click (sentinel-poisoned) | **verified-live** |
| Premium popup | text "Unlock 45+ premium features" + X ("xmark") | visual |
| Accommodations glitch page | text "Set up learning supports" / "Add Accommodations" | **verified-live** (glitch observed) |
| Glitch back arrow | role button **"chevron-left"** → "Back" → Escape → go_back | **verified-live** (recovery observed; first candidate is inferred from the app-wide back icon) |

## Maintenance

Re-verify after any Wayground UI change with:

```
wqu discover https://wayground.com/activity/admin/assessment
```

That saves HTML/aria/screenshots under `./snaps/`. Update this table and the
`status`/`evidence` fields in `wqu/selectors.py` with the new date.
