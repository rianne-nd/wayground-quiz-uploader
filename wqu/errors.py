"""Exception taxonomy for wqu.

Core modules raise these; ``wqu.cli`` maps them to human messages and exit
codes. Simple subclasses are deliberate: a GUI/CLI can distinguish failure
*kinds* without parsing strings.

Exit-code contract (enforced by cli.py):
  1 -> one or more files failed during a batch (summary still printed)
  2 -> configuration/session/environment error (re-login needed, class not
       found, bad arguments, missing file)
"""

from __future__ import annotations


class WquError(Exception):
    """Base class for all wqu errors."""


class SessionExpiredError(WquError):
    """No usable stored session; the user must run ``wqu login`` again."""


class SelectorNotFoundError(WquError):
    """None of a selector's candidate locators matched on the page."""

    def __init__(self, key: str, note: str = "") -> None:
        detail = f"Could not find UI element {key!r}."
        if note:
            detail += f" {note}"
        super().__init__(detail)
        self.key = key


class ValidationError(WquError):
    """Local .xlsx validation failed (sheet/headers/answers)."""

    def __init__(self, message: str, issues: list[str] | None = None) -> None:
        super().__init__(message)
        self.issues: list[str] = list(issues or [])


class UploadError(WquError):
    """The import modal reported errors (e.g. red banner) or upload failed."""

    def __init__(self, message: str, rows: list[int] | None = None, banner: str | None = None) -> None:
        super().__init__(message)
        self.rows: list[int] = list(rows or [])
        self.banner: str | None = banner


class PublishError(WquError):
    """The publish modal refused the submission (disabled button etc.)."""


class AssignError(WquError):
    """The assignment flow failed."""


class ClassNotFoundError(AssignError):
    """Requested class was not offered in the "Assign to a class" modal."""

    def __init__(self, class_name: str, available: list[str] | None = None) -> None:
        self.class_name = class_name
        self.available: list[str] = list(available or [])
        detail = f"Class {class_name!r} not found."
        if self.available:
            detail += " Available classes: " + ", ".join(self.available)
        else:
            detail += " (no classes in this account)"
        super().__init__(detail)


class ClassCreateError(AssignError):
    """Creating a class on the Wayground classes page failed."""


class JoinLinkError(WquError):
    """No join link could be extracted (never fabricate one)."""


class LibraryError(WquError):
    """Library page interaction failed (scan/select/archive)."""


class LibraryLimitError(LibraryError):
    """The account's active-quiz cap would be exceeded and nothing was archived."""
