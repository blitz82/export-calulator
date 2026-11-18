"""Local PDF and image extraction for review in the declaration ledger."""
import io
import json
import math
import re
import subprocess
import sys
import tempfile
from contextlib import closing
from functools import lru_cache
from pathlib import Path
from threading import RLock

from epms import rules

MAX_BYTES = 20_000_000
MAX_PAGES = 30
EXTRACTION_VERSION = 7
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png"}
CORE_FIELDS = {"number", "declared", "line", "hs", "kg", "usd"}
PDF_LOCK = RLock()  # PDFium calls must be serialized within this process.
SCRIPT = Path(__file__).with_name("windows_ocr.ps1")
NUMBER = r"[0-9]{5}-?[0-9]{2}-?[0-9]{5}(?:[0-9][A-Za-z]|[A-Za-z0-9])"
DATE = r"(?:[0-9]{8}|[0-9]{4}[./-][0-9]{1,2}[./-][0-9]{1,2})"
DECIMAL = r"[0-9]+(?:,[0-9]{3})*(?:\.[0-9]+)?"
LINE = r"(?:실제\s*)?(?:품목란(?:\s*번호)?|란\s*번호)\s*[:：]?\s*([0-9]{1,8})(?!\d)"
FIELDS = {
    "number": rf"(?:수출\s*)?신고\s*번호\s*[:：]?\s*({NUMBER})(?![A-Za-z0-9])",
    "declared": rf"신고\s*일(?:자)?\s*[:：.•]?\s*({DATE})(?!\d)",
    "shipped": rf"선적\s*일(?:자)?\s*[:：.•]?\s*({DATE})(?!\d)",
    "accepted": rf"(?:신고\s*)?수리\s*일(?:자)?\s*[:：.•]?\s*({DATE})(?!\d)",
    "hs": r"(?:HS\s*(?:코드|CODE)|세번부호)\s*[:：]?\s*([0-9]{4}[.\s-]?[0-9]{2}[.\s-]?[0-9]{4})(?!\d)",
    "fob": rf"(?:품목란\s*)?FOB\s*[:：]?\s*\(?\s*(?:USD|US\$|\$)\s*\)?\s*[:：]?\s*({DECIMAL})(?![\d.,])",
    "kg": rf"순\s*중량\s*[:：]?\s*(?:\(\s*kg\s*\)\s*[:：]?\s*({DECIMAL})(?![\d.,])|({DECIMAL})\s*kg\b)",
}
for role, label in (("agent", r"수출\s*대행자"), ("owner", r"수출\s*화주"), ("maker", "제조자")):
    FIELDS[role + "_name"] = rf"{label}[ \t]*(?:업체명|상호|성명)[ \t]*[:：]?[ \t]*([^\r\n]{{1,100}})"
    FIELDS[role] = rf"{label}\s*사업자\s*(?:등록\s*)?번호\s*[:：]?\s*(\d{{3}}-?\d{{2}}-?\d{{5}})(?!\d)"
OPTIONAL_OCR_ANCHORS = {
    "accepted": r"신고\s*수리\s*일자",
    "shipped": r"(?<![가-힣])선적\s*일자",
    "agent": r"수출\s*대행자\s*사업자\s*(?:등록\s*)?번호",
    "maker": r"제조자\s*사업자\s*(?:등록\s*)?번호",
}


@lru_cache(maxsize=1)
def unipass_template():
    return json.loads(SCRIPT.with_name("ocr_unipass.json").read_text(encoding="utf-8"))


def spaced(label):
    return r"\s*".join(map(re.escape, label))


def certificate_variant(header, template):
    titles = "|".join(spaced(title) for title in template["titles"])
    title = re.search(rf"(?:{titles})\s*\(([^)\r\n]{{0,30}})", header)
    if not title:
        return None
    qualifier = re.sub(r"\s+", "", rules.clean(title[1]))
    return next((variant["id"] for variant in template["variants"] if variant["qualifier"] == qualifier),
                "unspecified")


def page_heading(text):
    return "\n".join(text.splitlines()[:10])


def preview_box(box, rotation):
    left, top, right, bottom = box
    if rotation == 90:
        return 1 - bottom, left, 1 - top, right
    if rotation == 180:
        return 1 - right, 1 - bottom, 1 - left, 1 - top
    if rotation == 270:
        return top, 1 - right, bottom, 1 - left
    return box


def pdf_locator(layer, page):
    left, bottom, right, top = page.get_bbox()

    @lru_cache(maxsize=512)
    def locate(value):
        boxes = []
        with closing(layer.search(value)) as search:
            while hit := search.get_next():
                index, length = hit
                chars = [layer.get_charbox(i) for i in range(index, index + length)]
                box = (min(c[0] for c in chars), min(c[1] for c in chars),
                       max(c[2] for c in chars), max(c[3] for c in chars))
                boxes.append(((box[0] - left) / (right - left), (top - box[3]) / (top - bottom),
                              (box[2] - left) / (right - left), (top - box[1]) / (top - bottom)))
        return boxes

    return locate


def read_pdf_page(page):
    rotation = page.get_rotation()
    # PDFium's reading order can change with rotation. Parse in authoring
    # orientation, then restore the page before it is rendered for review.
    if rotation:
        page.set_rotation(0)
    try:
        with closing(page.get_textpage()) as layer:
            text = layer.get_text_bounded()[:32000]
            rows = unipass_rows(text, pdf_locator(layer, page), "PDF 텍스트")
    finally:
        if rotation:
            page.set_rotation(rotation)
    return text, rotate_evidence(rows, rotation)


def rotate_evidence(rows, rotation):
    return [(values, {key: dict(source, box=preview_box(source["box"], rotation)) if "box" in source else source
                      for key, source in evidence.items()}) for values, evidence in rows]


def windows_locator(payload):
    width, height = payload.get("width"), payload.get("height")
    if not width or not height or payload.get("angle"):
        return lambda _: []
    lines = payload["lines"]

    def locate(value):
        wanted = re.sub(r"\s+", "", value).casefold()
        if not wanted:
            return []
        boxes = []
        for line in lines:
            words = line["words"]
            for start in range(len(words)):
                single = re.sub(r"\s+", "", words[start]["text"]).casefold()
                if single != wanted and (
                    re.fullmatch(rf"[($]*{re.escape(wanted)}[)]*", single)
                    or (wanted.isalpha() and wanted in single)
                ):
                    x, y, w, h = words[start]["rect"]
                    boxes.append((x / width, y / height, (x + w) / width, (y + h) / height))
                    continue
                found = ""
                rects = []
                for word in words[start:]:
                    found += re.sub(r"\s+", "", word["text"]).casefold()
                    if not wanted.startswith(found):
                        break
                    x, y, w, h = word["rect"]
                    rects.append((x, y, x + w, y + h))
                    if found == wanted:
                        boxes.append((min(r[0] for r in rects) / width, min(r[1] for r in rects) / height,
                                      max(r[2] for r in rects) / width, max(r[3] for r in rects) / height))
                        break
        return boxes

    return locate


def spatial_ocr_lines(payload):
    if payload.get("angle"):
        return payload["lines"]
    words = [word for line in payload["lines"] for word in line["words"] if word["rect"][3] > 0]
    if not words:
        return payload["lines"]
    def center(word):
        return word["rect"][1] + word["rect"][3] / 2

    # Different fonts in this table can shift word centers within one printed row.
    # Join the columns only when the three item labels coexist near the same y.
    item_bands = []
    anchors = [word for word in words if "세번부호" in word["text"].replace(" ", "")]
    for anchor in anchors:
        if any(anchor in band for band in item_bands):
            continue
        band = [word for word in words if abs(center(word) - center(anchor)) <= .013 * payload["height"]]
        joined = "".join(word["text"] for word in band).replace(" ", "")
        if "순중량" in joined and "신고가격" in joined:
            item_bands.append(band)
    grouped = {id(word) for band in item_bands for word in band}

    bands = []
    for word in sorted((word for word in words if id(word) not in grouped), key=center):
        if not bands or center(word) - center(bands[-1][0]) > max(
                min(word["rect"][3], bands[-1][0]["rect"][3]) * .65, 2):
            bands.append([])
        bands[-1].append(word)
    lines = []
    for band in sorted(bands + item_bands, key=lambda group: min(map(center, group))):
        band.sort(key=lambda word: word["rect"][0])
        lines.append({"text": " ".join(word["text"] for word in band), "words": band})
    return lines


def scan_items(text, locate):
    # A row must include the commodity code, net weight and item price labels.
    # This excludes the document totals even if their labels are misread.
    for line in text.splitlines():
        if not all(re.search(spaced(label), line) for label in ("세번부호", "순중량", "신고가격")):
            continue
        item = {}
        patterns = {
            "hs": FIELDS["hs"],
            "kg": rf"순\s*중량\s*({DECIMAL})\s*\(?\s*kg\b",
            "usd": rf"(?<![가-힣])신고\s*가격[^$\r\n]*?\$\s*({DECIMAL})(?![\d.,])",
        }
        for key, pattern in patterns.items():
            matches = list(re.finditer(pattern, line, re.IGNORECASE))
            if len(matches) == 1:
                raw = matches[0][1]
                if key == "hs" or rules.decimal(raw) is not None:
                    boxes = locate(raw)
                    label = {"hs": "세번부호", "kg": "순중량", "usd": "신고가격"}[key]
                    anchors = locate(label)
                    near = [box for box in boxes if any(
                        -.008 <= box[1] - anchor[1] <= .017 and box[0] >= anchor[0]
                        for anchor in anchors)]
                    if len(near) == 1:
                        boxes = near
                    item[key] = (raw, boxes)
        if item:
            yield item


def unipass_rows(text, locate, method):
    template = unipass_template()
    # Use the page heading. The footer also mentions the certificate.
    header = page_heading(text)
    if not certificate_variant(header, template):
        return []

    def capture(pattern, source):
        found = {match.group(1).strip() for match in re.finditer(pattern, source, re.IGNORECASE)}
        return found.pop() if len(found) == 1 else ""

    def record(values, evidence, key, value, boxes=None):
        if not value:
            return
        cleaned = rules.clean(value)
        if key in {"declared", "accepted", "shipped"} and not rules.day(cleaned):
            return
        if key in {"usd", "kg", "quantity"} and rules.decimal(cleaned) is None:
            return
        values[key] = cleaned
        source = evidence[key] = {"raw": value, "normalized": cleaned, "method": method, "status": "AUTO_FILLED"}
        boxes = locate(value) if boxes is None else boxes
        if len(boxes) == 1 and all(0 <= coord <= 1 for coord in boxes[0]):
            source["box"] = boxes[0]

    def adjacent(value, label, *, excluded_label=""):
        if not value:
            return []
        boxes = locate(value)
        if len(boxes) <= 1:
            return boxes
        anchors = locate(label)
        if excluded_label:
            excluded = locate(excluded_label)
            anchors = [anchor for anchor in anchors if not any(
                box[0] <= anchor[0] < box[2] and abs(box[1] - anchor[1]) < 0.012
                for box in excluded)]
        return [box for box in boxes if any(
            -0.012 <= box[1] - anchor[1] <= 0.035 and
            -0.015 <= box[0] - anchor[0] <= 0.15 for anchor in anchors)]

    def header_value(key, pattern):
        label = template["headerAnchors"][key]
        anchors = [box for box in locate(label) if box[1] < 0.2]
        nearby = {}
        source = "\n".join(text.splitlines()[:24]) if method == "Windows OCR" else header
        for match in re.finditer(pattern, source, re.IGNORECASE):
            value = match[0]
            boxes = [box for box in locate(value) if any(
                abs(box[0] - anchor[0]) < 0.035 and -0.003 <= box[1] - anchor[3] <= 0.035
                for anchor in anchors)]
            if boxes:
                nearby[value] = boxes
        if len(nearby) == 1:
            return next(iter(nearby.items()))
        value = capture(FIELDS[key], header)
        if not value:
            table = re.search(r"신고\s*번호[^\n]*신고\s*일자[^\n]*\n([^\n]+)", header)
            if table:
                value = capture(rf"(?<![A-Za-z0-9])({pattern})", table[1])
        return value, locate(value) if value else []

    common, common_evidence = {}, {}
    for key, pattern in (("number", NUMBER + r"(?![A-Za-z0-9])"), ("declared", DATE + r"(?!\d)")):
        value, boxes = header_value(key, pattern)
        record(common, common_evidence, key, value, boxes)
    accepted = capture(FIELDS["accepted"], text)
    record(common, common_evidence, "accepted", accepted, adjacent(accepted, "수리일자"))
    shipped = capture(r"(?<![가-힣])" + FIELDS["shipped"], text)
    record(common, common_evidence, "shipped", shipped, adjacent(shipped, "선적일자"))
    record(common, common_evidence, "version",
           capture(r"정정\s*(?:버전|차수)\s*[:：]?\s*(\d{1,8})(?!\d)", header))

    marker = "|".join(spaced(label) for label in template["lineMarkers"])
    boundaries = list(re.finditer(rf"(?:{marker})", text))
    parties_text = text[:boundaries[0].start()] if boundaries else text
    owner = re.search(r"수\s*출\s*화\s*주(?P<body>.*?)제\s*조\s*자", parties_text, re.DOTALL)
    if owner:
        record(common, common_evidence, "owner",
               capture(rf"{spaced('사업자등록번호')}\s*\)?\s*[:：]?\s*(\d{{3}}-?\d{{2}}-?\d{{5}})(?!\d)",
                       owner["body"]))
    for key, label in (("agent_name", "수출대행자"), ("owner_name", "수출화주"), ("maker_name", "제조자")):
        value = capture(rf"{spaced(label)}[ \t:：]+([^\n\r(]{{2,80}})(?=[\n\r(])", parties_text)
        if re.search(r"[A-Za-z가-힣]", value):
            record(common, common_evidence, key, value)
    for role in ("agent", "maker"):
        record(common, common_evidence, role, capture(FIELDS[role], parties_text))
    summary = "|".join(spaced(label) for label in template["summaryMarkers"])
    rows = []
    for index, boundary in enumerate(boundaries):
        end = boundaries[index + 1].start() if index + 1 < len(boundaries) else len(text)
        block = re.split(summary, text[boundary.end():end], maxsplit=1)[0]
        values, evidence = dict(common), dict(common_evidence)
        item_number = re.match(r"\s*[:：]?\s*(\d{1,8})\s*/\s*(\d{1,8})(?![\d/])", block)
        if item_number:
            line, total = item_number.groups()
            if 0 < int(line) <= int(total):
                record(values, evidence, "line", line, locate(f"{line}/{total}"))
            block = block[item_number.end():]
        record(values, evidence, "hs", capture(FIELDS["hs"], block))
        kg = capture(rf"순\s*중량\s*[:：]?\s*({DECIMAL})\s*\(?\s*kg\b", block)
        record(values, evidence, "kg", kg, adjacent(kg, "순중량", excluded_label="총순중량"))
        usd = capture(rf"신고\s*가격\s*\(\s*F\s*O\s*B\s*\)\s*(?:USD|US\$|\$)\s*({DECIMAL})(?![\d.,])", block)
        record(values, evidence, "usd", usd, adjacent(usd, "신고가격(FOB)", excluded_label="총신고가격"))
        record(values, evidence, "serial", capture(r"일\s*련\s*번\s*호\s*[:：]?\s*(\d{1,12})(?!\d)", block))
        if method == "Windows OCR":
            items = list(scan_items(block, locate))
            if len(items) == 1:
                for key, (raw, boxes) in items[0].items():
                    if key not in values:
                        record(values, evidence, key, raw, boxes)
        if "usd" in values:
            values["fob"] = values["usd"]
        if values.keys() - common.keys():
            rows.append((values, evidence))
    if not boundaries and method == "Windows OCR":
        for item in scan_items(text, locate):
            values, evidence = dict(common), dict(common_evidence)
            for key, (raw, boxes) in item.items():
                record(values, evidence, key, raw, boxes)
            if "usd" in values:
                values["fob"] = values["usd"]
            rows.append((values, evidence))
    return rows or [(common, common_evidence)]


def candidates(text):
    text = rules.clean(text)
    numbers = {match.group(1) for match in re.finditer(FIELDS["number"], text)}
    boundaries = list(re.finditer(LINE, text))
    # Page totals and unlabelled numbers cannot be assigned to a commodity line.
    common = text[:boundaries[0].start()] if boundaries else text
    rows = []
    for index, boundary in enumerate(boundaries):
        end = boundaries[index + 1].start() if index + 1 < len(boundaries) else len(text)
        rows.append((boundary.group(1), text[boundary.end():end]))
    result = []
    for line, block in rows or [("", "")]:
        values = {"line": line} if line else {}
        for key, pattern in FIELDS.items():
            if key in {"fob", "kg", "hs"}:
                source = "\n".join(row for row in block.splitlines()
                                   if not re.search(r"총\s*(?:신고|중량|순중량|금액)|합\s*계", row))
            else:
                source = common + "\n" + block if len(numbers) <= 1 else block
            matches = {rules.clean(next(value for value in found.groups() if value))
                       for found in re.finditer(pattern, source, re.IGNORECASE)}
            if len(matches) == 1:
                values[key] = matches.pop()
        if "fob" in values:
            values["usd"] = values["fob"]
        result.append(values)
    return result


def open_pdf(data):
    import pypdfium2 as pdfium

    if not data.startswith(b"%PDF-"):
        raise ValueError("PDF 파일을 선택하세요.")
    try:
        return pdfium.PdfDocument(data)
    except pdfium.PdfiumError as error:
        raise ValueError("PDF를 열 수 없습니다. 암호가 없고 손상되지 않은 PDF를 선택하세요.") from error


def render(page, *, rotation=0):
    width, height = page.get_size()
    if not (0 < width <= 14400 and 0 < height <= 14400):
        raise ValueError("PDF 페이지 크기가 지원 범위를 벗어났습니다.")
    with closing(page.render(scale=min(3, 2000 / max(width, height)), rotation=rotation)) as bitmap:
        output = io.BytesIO()
        bitmap.to_pil().convert("RGB").save(output, format="PNG")
        return output.getvalue()


def image_preview(data):
    from PIL import Image, ImageOps

    try:
        with Image.open(io.BytesIO(data), formats=["JPEG", "PNG"]) as image:
            if image.width * image.height > 50_000_000:
                raise ValueError("그림의 해상도를 낮춰 다시 올려 주세요.")
            image.thumbnail((2000, 2000))
            corrected = ImageOps.exif_transpose(image).convert("RGBA")
            preview = Image.new("RGB", corrected.size, "white")
            preview.paste(corrected, mask=corrected.getchannel("A"))
            output = io.BytesIO()
            preview.save(output, format="PNG")
            return output.getvalue()
    except (OSError, Image.DecompressionBombError) as error:
        raise ValueError("그림을 열 수 없습니다. 손상되지 않은 JPG 또는 PNG 파일을 선택하세요.") from error


def page_image(data, page_number, *, is_image=False, rotation=0):
    if is_image:
        if page_number != 1:
            raise ValueError("원본 쪽 번호를 확인하세요.")
        return image_preview(data)
    with PDF_LOCK, open_pdf(data) as document:
        if not 1 <= page_number <= len(document):
            raise ValueError("PDF 쪽 번호를 확인하세요.")
        with closing(document[page_number - 1]) as page:
            return render(page, rotation=rotation)


def recognize(image):
    if sys.platform != "win32":
        return {}, "스캔 OCR은 Windows의 한국어 OCR 언어팩이 필요합니다."
    with tempfile.TemporaryDirectory(prefix="epms-ocr-") as folder:
        file = Path(folder) / "page.png"
        file.write_bytes(image)
        try:
            result = subprocess.run(
                ["powershell.exe", "-NoProfile", "-NonInteractive",
                 "-File", str(SCRIPT), str(file)],
                capture_output=True, encoding="utf-8", errors="replace", timeout=15,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        except (OSError, subprocess.TimeoutExpired):
            return {}, "Windows OCR을 실행하지 못했습니다. 원본을 보고 입력하세요."
    if result.returncode:
        return {}, "Windows OCR을 사용할 수 없습니다. 한국어 OCR 언어팩과 PowerShell 실행 정책을 확인하세요."
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        payload = None
    if valid_ocr_payload(payload):
        return payload, ""
    return {}, "Windows OCR 결과를 읽을 수 없습니다. 원본을 보고 입력하세요."


def valid_ocr_payload(payload):
    def numeric(value):
        return type(value) in (int, float) and math.isfinite(value)

    def valid_line(line):
        return (isinstance(line, dict) and isinstance(line.get("text"), str)
                and isinstance(line.get("words"), list)
                and all(isinstance(word, dict) and isinstance(word.get("text"), str)
                        and isinstance(word.get("rect"), list) and len(word["rect"]) == 4
                        and all(numeric(n) for n in word["rect"]) for word in line["words"]))

    return (isinstance(payload, dict) and all(type(payload.get(k)) is int and payload[k] > 0 for k in ("width", "height"))
            and (payload.get("angle") is None or numeric(payload["angle"]))
            and isinstance(payload.get("lines"), list) and all(valid_line(line) for line in payload["lines"]))


def read_image(image, *, enhance=False):
    from PIL import Image, ImageOps

    payload, warning = recognize(image)
    if not payload:
        return "", [], warning
    payload = dict(payload, lines=spatial_ocr_lines(payload))
    text = "\n".join(line["text"] for line in payload["lines"])[:32000]
    locate = windows_locator(payload)
    rows = unipass_rows(text, locate, "Windows OCR")
    if len(rows) == 1:
        values, evidence = rows[0]
        if not values.get("line"):
            retry_item_number(image, locate, values, evidence)
        retry_item_values(image, locate, values, evidence)
    if enhance and not any(values for values, _ in rows):
        with Image.open(io.BytesIO(image)) as original:
            output = io.BytesIO()
            ImageOps.autocontrast(original.convert("L")).save(output, format="PNG")
        retry_text, retry_rows, retry_warning = read_image(output.getvalue())
        if any(values for values, _ in retry_rows) or not text:
            return retry_text, retry_rows, retry_warning
    return text, rows, warning


def crop_evidence(payload, region, raw, normalized):
    source = {"raw": raw, "normalized": normalized, "method": "Windows OCR", "status": "AUTO_FILLED"}
    boxes = windows_locator(payload)(raw)
    if len(boxes) == 1:
        left, top, right, bottom = region
        a, b, c, d = boxes[0]
        source["box"] = (left + a * (right - left), top + b * (bottom - top),
                         left + c * (right - left), top + d * (bottom - top))
    return source


def retry_item_values(image, locate, values, evidence):
    hs_anchor = locate("세번부호")
    if len(hs_anchor) != 1:
        return
    y = hs_anchor[0][1]
    kg = [box for box in locate("순중량") if abs(box[1] - y) <= .018]
    usd = [box for box in locate("신고가격") if abs(box[1] - y) <= .018]
    if len(kg) != 1 or len(usd) != 1:
        return
    xh, xk, xu = hs_anchor[0][0], kg[0][0], usd[0][0]
    if not xh < xk < xu:
        return
    fields = (
        ("hs", xh, xk, FIELDS["hs"]),
        ("kg", xk, min(xu, xk + .24), rf"({DECIMAL})\s*\(?\s*kg\b"),
        ("usd", xu, 1, rf"\$\s*({DECIMAL})(?![\d.,])"),
    )
    for key, left, right, pattern in fields:
        if key in values:
            continue
        bitmap, region = enlarged_region(image, (max(0, left - .008), max(0, y - .012),
                                                 right, min(1, y + .029)), contrast=True)
        payload, _ = recognize(bitmap)
        if not payload:
            continue
        payload = dict(payload, lines=spatial_ocr_lines(payload))
        text = "\n".join(line["text"] for line in payload["lines"])
        found = set(re.findall(pattern, text, re.IGNORECASE))
        if len(found) != 1:
            continue
        raw = found.pop()
        if key in {"kg", "usd"} and rules.decimal(raw) is None:
            continue
        values[key] = raw
        evidence[key] = crop_evidence(payload, region, raw, raw)
        if key == "usd":
            values["fob"] = raw


def retry_item_number(image, locate, values, evidence):
    anchors = locate("란번호/총란수") or locate("규격")
    # Avoid assigning a crop to multiple item rows. Retry only near observed labels.
    candidates = {}
    for x0, y0, _, y1 in anchors[:4]:
        cropped, region = enlarged_region(image, (max(0, x0 - .015), max(0, y0 - .012),
                                                  min(1, x0 + .38), min(1, y1 + .032)), contrast=True)
        payload, _ = recognize(cropped)
        if not payload:
            continue
        payload = dict(payload, lines=spatial_ocr_lines(payload))
        text = "\n".join(line["text"] for line in payload["lines"])
        marker = re.search(r"란\s*번호\s*/\s*[총종]\s*란\s*수", text)
        if not marker:
            continue
        matches = set(re.findall(r"(?<![\d/])(\d{1,8})\s*/\s*(\d{1,8})(?![\d/])", text[marker.end():]))
        if len(matches) != 1:
            continue
        line, total = matches.pop()
        if not 0 < int(line) <= int(total):
            continue
        raw = f"{line}/{total}"
        candidates[(line, total)] = crop_evidence(payload, region, raw, line)
    if len(candidates) == 1:
        (line, _), source = next(iter(candidates.items()))
        values["line"] = line
        evidence["line"] = source


def enlarged_region(data, box, *, contrast=False):
    from PIL import Image, ImageOps

    with Image.open(io.BytesIO(data)) as image:
        x0, y0, x1, y1 = box
        width, height = image.size
        bounds = (int(x0 * width), int(y0 * height), min(width, int(x1 * width) + 1),
                  min(height, int(y1 * height) + 1))
        crop = image.crop(bounds)
        if contrast:
            crop = ImageOps.autocontrast(crop.convert("L"))
        scale = min(3, 2000 / max(crop.size))
        crop = crop.resize(tuple(max(1, round(n * scale)) for n in crop.size), Image.Resampling.LANCZOS)
        output = io.BytesIO()
        crop.save(output, format="PNG")
    left, top, right, bottom = bounds
    return output.getvalue(), (left/width, top/height, right/width, bottom/height)


def recognize_region(data, page, box):
    return recognize_crop(page_image(data, page), box)


def recognize_crop(image, box):
    x0, y0, x1, y1 = box
    cropped, _ = enlarged_region(image, (max(0, x0 - .004), max(0, y0 - .004),
                                          min(1, x1 + .004), min(1, y1 + .004)))
    payload, warning = recognize(cropped)
    return {"text": "\n".join(line["text"] for line in payload.get("lines", [])), "warning": warning}


def comparison_value(key, value):
    value = rules.clean(value)
    if key in {"usd", "kg", "quantity"}:
        parsed = rules.decimal(value)
        return value if parsed is None else parsed
    if key in {"declared", "accepted", "shipped"}:
        return rules.day(value) or value
    if key in {"number", "agent", "owner", "maker", "hs"}:
        return re.sub(r"[-.\s]", "", value).upper()
    return value


def row_identity(values):
    return tuple(comparison_value(key, values.get(key, "")) for key in ("number", "line"))


def merge_rows(primary, secondary):
    if not primary:
        return secondary
    for values, evidence in primary:
        identity = row_identity(values)
        matches = [(row, source) for row, source in secondary
                   if all(identity) and row_identity(row) == identity]
        if not matches and len(primary) == len(secondary) == 1:
            other = secondary[0][0]
            other_identity = row_identity(other)
            pairs = list(zip(identity, other_identity))
            if any(a and a == b for a, b in pairs) and all(not a or not b or a == b for a, b in pairs):
                matches = secondary
        if len(matches) != 1:
            continue
        row, source = matches[0]
        for key, value in row.items():
            if key == "fob":
                continue
            if key not in values:
                values[key] = value
                if key in source:
                    evidence[key] = source[key]
            elif comparison_value(key, values[key]) != comparison_value(key, value):
                evidence[key] = dict(evidence.get(key, {}), status="CONFLICT", alternate=value,
                                     alternate_evidence=source.get(key, {}))
        if "usd" in values:
            values["fob"] = values["usd"]
    return primary


def has_scan_image(page):
    import pypdfium2.raw as pdfium

    left, bottom, right, top = page.get_bbox()
    page_area = (right - left) * (top - bottom)
    for image in page.get_objects(filter=[pdfium.FPDF_PAGEOBJ_IMAGE]):
        x0, y0, x1, y1 = image.get_bounds()
        coverage = max(0, min(x1, right) - max(x0, left)) * max(0, min(y1, top) - max(y0, bottom))
        if coverage >= 0.5 * page_area and coverage > 0:
            return True
    return False


def needs_ocr(text, rows):
    if not text.strip():
        return True
    if not rows:
        return False
    expected = CORE_FIELDS | {key for key, anchor in OPTIONAL_OCR_ANCHORS.items() if re.search(anchor, text)}
    return any(expected - values.keys() for values, _ in rows)


def extracted_page(page, text, method, warning, matches):
    if not matches and not warning:
        warning = "지원되는 수출신고필증 서식을 확인하지 못했습니다."
    template = unipass_template()
    variant = certificate_variant(page_heading(text), template)
    totals = {}
    if variant:
        for key, pattern in (
            ("total_kg", rf"총\s*중량\s*[:：]?\s*({DECIMAL})\s*\(?\s*kg\b"),
            ("fob_usd", rf"총\s*신고\s*가격\s*\(\s*FOB\s*\)\s*\$\s*({DECIMAL})(?![\d.,])"),
        ):
            found = {match for match in re.findall(pattern, text, re.IGNORECASE) if rules.decimal(match) is not None}
            if len(found) == 1:
                totals[key] = found.pop()
    return dict(page=page, text=text, method=method, warning=warning, extractor_version=EXTRACTION_VERSION,
                template={"id": template["id"], "version": template["version"], "variant": variant} if variant else None,
                document_totals=totals, detected_items=len(matches),
                rows=[values for values, _ in matches], evidence=[source for _, source in matches])


def stitch_continuations(pages):
    """Combine only consecutive pages of the same recognized declaration."""
    previous = None
    for page in pages:
        rows = list(zip(page["rows"], page["evidence"]))
        variant = (page["template"] or {}).get("variant", "")
        families = {row_identity(row)[0] for row, _ in rows}
        versions = {row.get("version", "") for row, _ in rows}
        if (len(families) != 1 or "" in families or len(versions) != 1
                or variant not in {"preloading-cover", "completed-cover",
                                   "preloading-continuation", "completed-continuation"}):
            previous = None
            continue
        stage, sheet = variant.split("-", 1)
        identity = families.pop(), versions.pop(), stage
        if not identity[1] and re.search(r"정정", page_heading(page["text"])):
            previous = None
            continue
        if previous is None or previous[0] != identity or sheet == "cover":
            previous = identity, {}
        known = previous[1]
        counts = {}
        for row, _ in rows:
            line = row_identity(row)[1]
            counts[line] = counts.get(line, 0) + 1

        keep = []
        for row, evidence in rows:
            line = row_identity(row)[1]
            match = known.get(line) if line and counts[line] == 1 and sheet == "continuation" else None
            if match:
                base, source = match
                shared = (row.keys() & base.keys()) - {"number", "line", "fob"}
                if all(comparison_value(key, base[key]) == comparison_value(key, row[key]) for key in shared):
                    for key in row.keys() - base.keys():
                        base[key] = row[key]
                        if key in evidence:
                            source[key] = dict(evidence[key], page=page["page"])
                    continue
                page["warning"] = "이어지는 품목란의 값이 서로 다릅니다. 원본에서 정정 여부와 수치를 확인하세요."
            if line and not (line in known and known[line] is None):
                known[line] = (row, evidence) if counts[line] == 1 and not match else None
            keep.append((row, evidence))
        page["rows"] = [row for row, _ in keep]
        page["evidence"] = [evidence for _, evidence in keep]
    return pages


def extract(data, *, is_image=False):
    if is_image:
        text, rows, warning = read_image(image_preview(data), enhance=True)
        return [extracted_page(1, text, "Windows OCR", warning, rows)]
    pages = []
    with PDF_LOCK, open_pdf(data) as document:
        if not 1 <= len(document) <= MAX_PAGES:
            raise ValueError(f"PDF는 1~{MAX_PAGES}쪽이어야 합니다.")
        for index in range(len(document)):
            with closing(document[index]) as page:
                text, rows = read_pdf_page(page)
                method, warning = "PDF 텍스트", ""
                if needs_ocr(text, rows) or (not rows and has_scan_image(page)):
                    rotation = page.get_rotation()
                    recognized, scanned, warning = read_image(render(page, rotation=(-rotation) % 360),
                                                              enhance=not rows)
                    scanned = rotate_evidence(scanned, rotation)
                    if not rows and (scanned or (recognized and not text.strip())):
                        text, method = recognized, "Windows OCR"
                    elif rows and scanned:
                        method = "PDF 텍스트 + Windows OCR"
                    rows = merge_rows(rows, scanned)
                pages.append(extracted_page(index + 1, text, method, warning, rows))
    return stitch_continuations(pages)
