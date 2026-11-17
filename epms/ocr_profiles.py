"""Supplementary OCR templates for a recognized single-item electronic page."""
import re
from contextlib import closing
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from epms import ocr, rules


PATTERNS = {
    "declared": ocr.DATE,
    "shipped": ocr.DATE,
    "accepted": ocr.DATE,
    "hs": r"[0-9]{4}[. -]?[0-9]{2}[. -]?[0-9]{4}",
    "number": ocr.NUMBER,
}
FORMATS = {
    "date-separated": ({"declared", "shipped", "accepted"}, r"[0-9]{4}[./-][0-9]{1,2}[./-][0-9]{1,2}"),
    "date-compact": ({"declared", "shipped", "accepted"}, r"[0-9]{8}"),
    "hs-compact": ({"hs"}, r"[0-9]{10}"),
}


class Region(BaseModel):
    model_config = ConfigDict(extra="forbid")
    field: Literal["number", "declared", "shipped", "accepted", "hs"]
    box: tuple[float, float, float, float]
    anchor: str = Field(default="", max_length=40, pattern=r"^[A-Za-z0-9가-힣 /().·-]*$")
    format: Literal["default", "date-separated", "date-compact", "hs-compact"] = "default"

    @model_validator(mode="after")
    def validate_box(self):
        left, top, right, bottom = self.box
        minimum = -1 if self.anchor else 0
        if not (minimum <= left < right <= 1 and minimum <= top < bottom <= 1):
            raise ValueError("영역 사각형의 순서와 좌표 범위를 확인하세요.")
        if self.format != "default" and self.field not in FORMATS[self.format][0]:
            raise ValueError("필드에 해당하는 형식만 선택하세요.")
        return self


class Profile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=60)
    variant: Literal["preloading-cover", "preloading-continuation", "completed-cover", "completed-continuation"]
    regions: list[Region] = Field(min_length=1, max_length=5)

    @model_validator(mode="after")
    def unique_fields(self):
        if len({region.field for region in self.regions}) != len(self.regions):
            raise ValueError("필드마다 영역 하나만 지정하세요.")
        return self


def preview(page, profile):
    rotation = page.get_rotation()
    with closing(page.get_textpage()) as layer:
        text = layer.get_text_bounded()[:32000]
        if ocr.certificate_variant(ocr.page_heading(text), ocr.unipass_template()) != profile.variant:
            return {}
        left, bottom, right, top = page.get_bbox()
        locate = ocr.pdf_locator(layer, page)
        candidates = {}
        for region in profile.regions:
            box = region.box
            if region.anchor:
                hits = locate(region.anchor)
                if len(hits) != 1:
                    continue
                x, y, _, _ = ocr.preview_box(hits[0], rotation)
                box = (x + box[0], y + box[1], x + box[2], y + box[3])
            x0, y0, x1, y1 = box
            if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1):
                continue
            a, b, c, d = ocr.preview_box(box, (-rotation) % 360)
            raw = layer.get_text_bounded(left + a * (right-left), top - d * (top-bottom),
                                         left + c * (right-left), top - b * (top-bottom))[:500]
            pattern = PATTERNS[region.field] if region.format == "default" else FORMATS[region.format][1]
            values = set(re.findall(rf"(?<![A-Za-z0-9])(?:{pattern})(?![A-Za-z0-9])", raw))
            if len(values) != 1:
                continue
            value = values.pop()
            if region.field in {"declared", "shipped", "accepted"} and not rules.day(value):
                continue
            candidates[region.field] = {"raw": value, "normalized": value, "box": box, "method": profile.name,
                                        "status": "AUTO_FILLED"}
        return candidates


def apply(data, pages, profile):
    with ocr.PDF_LOCK, ocr.open_pdf(data) as document:
        for result in pages:
            if (len(result["rows"]) != 1 or (result["template"] or {}).get("variant") != profile.variant
                    or "PDF 텍스트" not in result["method"]):
                continue
            with closing(document[result["page"] - 1]) as page:
                candidates = preview(page, profile)
            row, evidence = result["rows"][0], result["evidence"][0]
            for field, candidate in candidates.items():
                if field not in row:
                    row[field] = candidate["normalized"]
                    evidence[field] = candidate
