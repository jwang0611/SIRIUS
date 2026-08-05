"""Structured, serializable results for Spec Mapper Excel write operations.

These models make the *actual* outcome of every workbook mutation observable:
how many operations were attempted, how many were really written, how many were
safely skipped, plus structured warnings and errors.

Safety contract (GxP / PHI): a :class:`WriteIssue` carries only a safe machine
``code``, a ``stage`` / ``operation`` label and coarse workbook coordinates
(sheet name, row, column, plus the SDTM ``variable`` name when the issue is
about one configured variable).  It MUST NOT contain absolute file paths, raw
clinical values, API keys / tokens, Python tracebacks, or full exception text.
``detail`` is reserved for a short, safe hint such as the exception *class*
name (``"ValueError"``) — never the exception message.

The counting invariant per stage is::

    attempted == written + skipped + len(errors)

``warnings`` are advisory annotations (e.g. a SUPP label that is too long); they
are orthogonal to the written/skipped/error split and do not affect the
invariant.  A run is considered fully successful only when
``written == attempted`` across every stage and no errors were recorded.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE


class RecoverableWriteError(Exception):
    """A *recognized*, per-item recoverable write precondition.

    Raise this (never a bare ``ValueError``) when a specific, known write
    precondition fails for one item and the run should continue: the offending
    item is recorded as a structured error while the rest of the workbook is
    still written and saved.

    Only this dedicated type is downgraded to a structured write error at the
    single-call ``_guard`` boundary. Every other exception (``ValueError``,
    ``AttributeError``, ``TypeError``, ``KeyError``, ``OSError``,
    ``RuntimeError``, openpyxl's ``IllegalCharacterError``, ...) is treated as
    unknown/fatal and propagates so the job is marked ``failed`` — a broad
    ``except`` at a call boundary would otherwise mask genuine bugs behind a
    "recoverable" label, or save a workbook whose counts do not reflect a
    partial, mid-item mutation.

    Per-item write loops therefore do NOT downgrade exceptions at all. Instead
    they *pre-validate* each item (via :func:`contains_illegal_characters` and
    explicit sheet/target checks) **before** performing any workbook mutation
    for it: an invalid item is recorded as a structured error with zero
    mutation, so a recorded "recoverable" outcome never coexists with a
    half-written row or a destructively-modified block.
    """


def contains_illegal_characters(*values: Any) -> bool:
    """True when any value's string form would be rejected by openpyxl.

    Mirrors openpyxl's own cell-write check (``ILLEGAL_CHARACTERS_RE`` over
    ``str`` values — control characters that cannot be stored in XLSX). Used to
    pre-validate a whole item before any destructive workbook operation, so an
    ``IllegalCharacterError`` can never fire mid-item and leave a half-written
    row behind.
    """
    return any(isinstance(value, str) and ILLEGAL_CHARACTERS_RE.search(value) for value in values)


# --- stage name constants (operation groups tracked by SpecMapper.process) ----
STAGE_CELL_UPDATES = "cell_updates"
STAGE_SUPP_ROWS = "supp_rows"
STAGE_UNMATCHED_ROWS = "unmatched_rows"
STAGE_CONDITIONAL_MAPPINGS = "conditional_mappings"
STAGE_CODELIST_RECORDS = "codelist_records"
STAGE_FIXED_VARIABLE_RULES = "fixed_variable_rules"
STAGE_FORMULAS_AND_LINKS = "formulas_and_links"
STAGE_SOURCE_COLUMNS = "source_columns"
STAGE_EXTERNAL_CODING = "external_coding"
STAGE_CONTENT_DOMAINS = "content_domains"
STAGE_STYLES = "styles"


@dataclass
class WriteIssue:
    """A single structured, non-sensitive write warning or error.

    Attributes:
        code: Safe machine-readable code (e.g. ``"sheet_not_found"``).
        stage: The operation group this issue belongs to (see STAGE_* constants).
        operation: The specific operation attempted (e.g. ``"update_cell"``).
        sheet: Worksheet name involved, if applicable.
        row: 1-based row involved, if applicable.
        column: 1-based column involved, if applicable.
        variable: SDTM variable name the issue is about, if applicable. Only a
            *configured / template* variable name (e.g. ``"AEDECOD"``) — never
            a subject value or any other clinical content.
        detail: Short safe hint (e.g. an exception *class* name). Never a raw
            exception message, path, token, or clinical value.
    """

    code: str
    stage: str
    operation: str
    sheet: str | None = None
    row: int | None = None
    column: int | None = None
    variable: str | None = None
    detail: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "code": self.code,
            "stage": self.stage,
            "operation": self.operation,
            "sheet": self.sheet,
            "row": self.row,
            "column": self.column,
            "detail": self.detail,
        }
        # ``variable`` is emitted only when it applies, so the serialized shape
        # of every pre-existing issue kind stays exactly as it was.
        if self.variable is not None:
            payload["variable"] = self.variable
        return payload


@dataclass
class StageWriteResult:
    """Attempted / written / skipped counts plus issues for one write stage."""

    stage: str
    attempted: int = 0
    written: int = 0
    skipped: int = 0
    warnings: list[WriteIssue] = field(default_factory=list)
    errors: list[WriteIssue] = field(default_factory=list)

    def record_written(self, count: int = 1) -> None:
        """Record ``count`` operations that successfully mutated the workbook."""
        self.attempted += count
        self.written += count

    def record_skipped(self, issue: WriteIssue | None = None, count: int = 1) -> None:
        """Record ``count`` operations that were safely not performed.

        An optional *issue* is appended to :attr:`warnings` to explain the skip.
        """
        self.attempted += count
        self.skipped += count
        if issue is not None:
            self.warnings.append(issue)

    def record_error(self, issue: WriteIssue) -> None:
        """Record one attempted operation that failed recoverably."""
        self.attempted += 1
        self.errors.append(issue)

    def add_warning(self, issue: WriteIssue) -> None:
        """Attach an advisory warning without changing counts."""
        self.warnings.append(issue)

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "attempted": self.attempted,
            "written": self.written,
            "skipped": self.skipped,
            "warnings": [w.to_dict() for w in self.warnings],
            "errors": [e.to_dict() for e in self.errors],
        }


@dataclass
class WriteResult:
    """Aggregate of per-stage write results for a whole ``process()`` run."""

    stages: dict[str, StageWriteResult] = field(default_factory=dict)

    def stage(self, name: str) -> StageWriteResult:
        """Get (creating if needed) the :class:`StageWriteResult` for *name*."""
        if name not in self.stages:
            self.stages[name] = StageWriteResult(stage=name)
        return self.stages[name]

    def merge_stage(self, result: StageWriteResult | None) -> None:
        """Merge a stand-alone stage result into this aggregate by stage name."""
        if result is None:
            return
        target = self.stage(result.stage)
        target.attempted += result.attempted
        target.written += result.written
        target.skipped += result.skipped
        target.warnings.extend(result.warnings)
        target.errors.extend(result.errors)

    @property
    def attempted(self) -> int:
        return sum(s.attempted for s in self.stages.values())

    @property
    def written(self) -> int:
        return sum(s.written for s in self.stages.values())

    @property
    def skipped(self) -> int:
        return sum(s.skipped for s in self.stages.values())

    @property
    def warning_count(self) -> int:
        return sum(len(s.warnings) for s in self.stages.values())

    @property
    def error_count(self) -> int:
        return sum(len(s.errors) for s in self.stages.values())

    @property
    def has_errors(self) -> bool:
        return self.error_count > 0

    @property
    def has_write_problems(self) -> bool:
        """True when any planned operation did not succeed (skips or errors)."""
        return self.written < self.attempted or self.error_count > 0

    def all_warnings(self) -> list[WriteIssue]:
        issues: list[WriteIssue] = []
        for s in self.stages.values():
            issues.extend(s.warnings)
        return issues

    def all_errors(self) -> list[WriteIssue]:
        issues: list[WriteIssue] = []
        for s in self.stages.values():
            issues.extend(s.errors)
        return issues

    def summary(self) -> dict[str, int]:
        """Compact, safe counts suitable for a job status / UI summary."""
        return {
            "attempted": self.attempted,
            "written": self.written,
            "skipped": self.skipped,
            "warnings": self.warning_count,
            "errors": self.error_count,
        }

    def to_dict(self) -> dict[str, Any]:
        """Full structured, JSON-serializable representation."""
        return {
            "summary": self.summary(),
            "stages": {name: s.to_dict() for name, s in self.stages.items()},
            "warnings": [w.to_dict() for w in self.all_warnings()],
            "errors": [e.to_dict() for e in self.all_errors()],
        }
