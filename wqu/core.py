"""The public, importable API of wqu — the only module a wrapper should import.

GUI-ready contract (brief Part 2.6):
  * every function returns dataclasses; nothing here prints or prompts;
  * long-running functions accept ``on_progress`` / ``should_cancel``;
  * browser state is an explicit ``Session`` object, config is
    ``PipelineOptions`` — no module-level mutable state, no asyncio.

REPL acid test:

    >>> from pathlib import Path
    >>> from wqu.core import publish_one, PipelineOptions
    >>> result = publish_one(Path("sample.xlsx"), PipelineOptions(assign_class="Naic"))
    >>> result.join_link
    'https://wayground.com/join?gc=24923411&source=liveDashboard'
"""

from __future__ import annotations

from wqu.assign import AssignResult, assign_one
from wqu.classes import (
    ClassChoice,
    CreatedClass,
    create_class,
    scrape_classes_from_modal,
)
from wqu.errors import (
    AssignError,
    ClassCreateError,
    ClassNotFoundError,
    JoinLinkError,
    PublishError,
    SessionExpiredError,
    UploadError,
    ValidationError,
    WquError,
)
from wqu.output import (
    NO_JOIN_LINK_MARKER,
    FileResult,
    copy_to_clipboard,
    format_json,
    format_links,
    format_summary,
    natural_sort_key,
    validate_join_link,
)
from wqu.publish import (
    DuplicateInfo,
    FileView,
    PipelineOptions,
    PublishResult,
    publish_many,
    publish_one,
)
from wqu.session import (
    CheckResult,
    DoctorCheck,
    LoginResult,
    Session,
    discover,
)
from wqu.upload import (
    Issue,
    QType,
    Question,
    QuizData,
    load_xlsx,
    preview_errors,
    title_from_filename,
)

__all__ = [
    # pipeline
    "PipelineOptions",
    "PublishResult",
    "AssignResult",
    "FileResult",
    "FileView",
    "DuplicateInfo",
    "publish_one",
    "publish_many",
    "assign_one",
    # session / diagnostics
    "Session",
    "LoginResult",
    "CheckResult",
    "DoctorCheck",
    "discover",
    # classes
    "ClassChoice",
    "CreatedClass",
    "create_class",
    "scrape_classes_from_modal",
    # workbook layer
    "QuizData",
    "Question",
    "QType",
    "Issue",
    "load_xlsx",
    "preview_errors",
    "title_from_filename",
    # output
    "format_links",
    "format_json",
    "format_summary",
    "natural_sort_key",
    "validate_join_link",
    "copy_to_clipboard",
    "NO_JOIN_LINK_MARKER",
    # errors
    "WquError",
    "SessionExpiredError",
    "ValidationError",
    "UploadError",
    "PublishError",
    "AssignError",
    "ClassNotFoundError",
    "ClassCreateError",
    "JoinLinkError",
]
