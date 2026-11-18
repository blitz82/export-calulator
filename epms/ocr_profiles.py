"""Supplementary OCR templates for recognized single-item pages."""
import re
from contextlib import closing
from typing import Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, model_validator

from epms import ocr, rules


RegionField = Literal[
    "number", "line", "version", "declared", "shipped", "accepted", "hs", "country", "product",
    "usd", "kg", "quantity", "unit", "agent", "agent_name", "owner", "owner_name", "maker", "maker_name",
]
REGION_FIELDS = get_args(RegionField)
BUSINESS_FIELDS = {"agent", "owner", "maker"}
TEXT_FIELDS = {"country", "product", "unit", "agent_name", "owner_name", "maker_name"}

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
    field: RegionField
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
    source: Literal["text", "scan"] = "text"
    regions: list[Region] = Field(min_length=1, max_length=len(REGION_FIELDS))

    @model_validator(mode="after")
    def unique_fields(self):
        if len({region.field for region in self.regions}) != len(self.regions):
            raise ValueError("필드마다 영역 하나만 지정하세요.")
        if self.source == "scan" and any(region.anchor for region in self.regions):
            raise ValueError("스캔용 템플릿은 고정 사각형만 지원합니다.")
        return self


def region_value(field, raw, date_or_hs_format="default"):
    raw = rules.clean(raw)
    if field in {"usd", "kg", "quantity"}:
        unit = {"usd": r"(?:USD|US\$|\$)", "kg": r"kg"}.get(field)
        if unit:
            match = re.fullmatch(rf"(?:{unit}\s*)?({ocr.DECIMAL})(?:\s*{unit})?", raw, re.IGNORECASE)
            raw = match[1] if match else ""
        value = rules.decimal(raw)
        return raw.replace(",", "") if value is not None and value > 0 else None
    if field in BUSINESS_FIELDS:
        if not re.fullmatch(r"[0-9]{3}[ -]?[0-9]{2}[ -]?[0-9]{5}", raw):
            return None
        number = rules.business_number(raw)
        return number if rules.valid_number(number) else None
    if field in {"line", "version"}:
        pattern = r"([0-9]{1,8})(?:\s*/\s*[0-9]{1,8})?" if field == "line" else r"([0-9]{1,8})"
        match = re.fullmatch(pattern, raw)
        return match[1] if match and int(match[1]) else None
    if field in TEXT_FIELDS:
        # A region containing multiple rows can include a neighboring item or label.
        limit = 200 if field == "product" else 100
        if len(raw) <= limit and len(raw.splitlines()) == 1 and any(char.isalpha() for char in raw):
            return re.sub(r"[^\S\r\n]+", " ", raw)
        return None
    pattern = PATTERNS[field] if date_or_hs_format == "default" else FORMATS[date_or_hs_format][1]
    values = set(re.findall(rf"(?<![A-Za-z0-9])(?:{pattern})(?![A-Za-z0-9])", raw))
    if len(values) != 1:
        return None
    value = values.pop()
    if field not in {"declared", "shipped", "accepted"} or rules.day(value):
        return value
    return None


def selection_values(raw):
    return {field: value for field in REGION_FIELDS
            if (value := region_value(field, raw)) is not None}


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
            if value := region_value(region.field, raw, region.format):
                candidates[region.field] = {"raw": raw.strip(), "normalized": value, "box": box,
                                            "method": profile.name, "status": "AUTO_FILLED"}
        return candidates


def preview_scan(data, result, profile, existing=()):
    if (result.get("method") != "Windows OCR" or result.get("detected_items", len(result["rows"])) > 1
            or (result.get("template") or {}).get("variant") != profile.variant):
        return {}
    regions = [region for region in profile.regions if region.field not in existing]
    if not regions:
        return {}
    image = ocr.page_image(data, result["page"])
    candidates = {}
    for region in regions:
        recognized = ocr.recognize_crop(image, region.box)
        if recognized["warning"]:
            break
        raw = recognized["text"]
        if value := region_value(region.field, raw, region.format):
            candidates[region.field] = {"raw": raw, "normalized": value, "box": region.box,
                                        "method": profile.name, "status": "AUTO_FILLED"}
    return candidates


def apply(data, pages, profile):
    if profile.source == "scan":
        for result in pages:
            if len(result["rows"]) == 1:
                fill(result, preview_scan(data, result, profile, result["rows"][0]))
        return
    with ocr.PDF_LOCK, ocr.open_pdf(data) as document:
        for result in pages:
            if (len(result["rows"]) != 1 or (result["template"] or {}).get("variant") != profile.variant
                    or "PDF 텍스트" not in result["method"]):
                continue
            with closing(document[result["page"] - 1]) as page:
                candidates = preview(page, profile)
            fill(result, candidates)


def fill(result, candidates):
    row, evidence = result["rows"][0], result["evidence"][0]
    for field, candidate in candidates.items():
        if field not in row:
            row[field] = candidate["normalized"]
            evidence[field] = candidate
            if field == "usd":
                row["fob"] = row[field]
