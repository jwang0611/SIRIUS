"""Orchestrate aCRF/eCRF PDF extraction into canonical records.

Flow: outline → form spans → per-form positioned text → deterministic field
candidates (+ optional LLM assist) → record assembly. The LLM path is only
touched when ``use_llm`` is set and a client is supplied.
"""

from __future__ import annotations

import logging

from src.processors.acrf import outline as _outline
from src.processors.acrf import text as _text
from src.processors.acrf.fields import extract_form_sections, merge_field_lists, validate_field_set
from src.processors.acrf.models import AcrfConfig, ExtractionResult, FormSpan, LineBox
from src.processors.acrf.records import assemble_records

logger = logging.getLogger(__name__)

# Above this fraction of Unicode replacement chars a form's text is garbled
# (subset fonts without ToUnicode); skip it rather than emit noise.
_MAX_REPLACEMENT_RATIO = 0.3
# Cap page text handed to the LLM so prompts stay bounded.
_LLM_PAGE_TEXT_LIMIT = 6000


def _form_line_boxes(span: FormSpan, boxes_by_page: dict[int, list[LineBox]]) -> list[LineBox]:
    boxes: list[LineBox] = []
    for page in range(span.page_start, span.page_end + 1):
        boxes.extend(boxes_by_page.get(page, []))
    return boxes


def _page_text(line_boxes: list[LineBox]) -> str:
    return "\n".join(lb.text for lb in line_boxes if lb.text.strip())


def extract_acrf(
    pdf_path: str,
    *,
    cfg: AcrfConfig | None = None,
    use_llm: bool = False,
    client: object | None = None,
    masker: object | None = None,
    language: str = "cn",
) -> ExtractionResult:
    """Extract (form, field) records from an aCRF/eCRF PDF.

    ``use_llm`` requires ``client`` (a :class:`BaseAIClient`). When enabled for
    a sparse form, validated LLM labels are merged into the deterministic list;
    model omissions never remove deterministic fields.
    """
    cfg = cfg or AcrfConfig()
    result = ExtractionResult()

    spans, skipped, total_pages = _outline.parse_outline(pdf_path, cfg)
    result.skipped_forms = skipped

    boxes_by_page, heights = _text.extract_all_line_boxes(pdf_path)
    boilerplate = _text.detect_boilerplate(boxes_by_page, heights, cfg.header_footer_band)

    llm_enabled = bool(use_llm and client is not None)
    if use_llm and client is None:
        result.warnings.append("use_llm requested but no client supplied; using deterministic extraction")

    form_fields: list[tuple[str, list[str]]] = []
    forms_with_fields = 0
    sub_forms = 0
    llm_forms = 0
    for span in spans:
        line_boxes = _form_line_boxes(span, boxes_by_page)
        if not line_boxes:
            result.warnings.append(f"form '{span.form_name}': no text on pages {span.page_start}-{span.page_end}")
            form_fields.append((span.form_name, []))
            continue
        if _text.replacement_char_ratio(line_boxes) > _MAX_REPLACEMENT_RATIO:
            result.warnings.append(f"form '{span.form_name}': garbled text (missing font ToUnicode); skipped")
            form_fields.append((span.form_name, []))
            continue

        page_height = heights.get(span.page_start)
        sections = extract_form_sections(
            line_boxes,
            boilerplate,
            cfg,
            page_height,
            form_name=span.form_name,
        )
        # A grid heading that merely repeats the bookmark name is not a separate
        # table; fold it back so its columns stay with the form.
        fields = [
            f for section in sections if section.name is None or section.name == span.form_name for f in section.fields
        ]

        # Only call the LLM when the deterministic pass came up short (avoids
        # sending every form to an external model), and MERGE its labels rather
        # than overwrite — a model omission must never drop a deterministic field.
        if llm_enabled and len(fields) < cfg.llm_min_fields:
            from src.processors.acrf.llm_extractor import extract_fields_llm

            page_text = _page_text(line_boxes)[:_LLM_PAGE_TEXT_LIMIT]
            llm_fields = extract_fields_llm(
                page_text,
                form_name=span.form_name,
                client=client,
                masker=masker,
                language=language,
            )
            if llm_fields:
                fields = merge_field_lists(fields, llm_fields)
                llm_forms += 1

        fields, warns = validate_field_set(fields, cfg)
        for w in warns:
            result.warnings.append(f"form '{span.form_name}': {w}")
        if fields:
            forms_with_fields += 1
        form_fields.append((span.form_name, fields))

        # A grid with its own in-page heading is a separate form in the ALS, so
        # emit it as its own table rather than folding it into the bookmark form.
        for section in sections:
            if section.name is None or section.name == span.form_name:
                continue
            sub_fields, sub_warns = validate_field_set(section.fields, cfg)
            for w in sub_warns:
                result.warnings.append(f"form '{section.name}': {w}")
            if sub_fields:
                sub_forms += 1
                form_fields.append((section.name, sub_fields))

    result.records = assemble_records(form_fields, cfg)
    result.stats = {
        "total_pages": total_pages,
        "forms_total": len(spans),
        "forms_skipped": len(skipped),
        "forms_with_fields": forms_with_fields,
        "forms_without_fields": len(spans) - forms_with_fields,
        "sub_forms": sub_forms,
        "forms_via_llm": llm_forms,
        "fields_total": len(result.records),
        "llm_enabled": llm_enabled,
    }
    return result
