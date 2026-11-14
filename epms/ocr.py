"""Local PDF and image extraction for review in the declaration ledger."""
import io
import re
import subprocess
import sys
import tempfile
from contextlib import closing
from pathlib import Path
from threading import RLock

from epms import rules

MAX_BYTES = 20_000_000
MAX_PAGES = 30
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png"}
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


def render(page):
    width, height = page.get_size()
    if not (0 < width <= 14400 and 0 < height <= 14400):
        raise ValueError("PDF 페이지 크기가 지원 범위를 벗어났습니다.")
    with closing(page.render(scale=min(3, 2000 / max(width, height)))) as bitmap:
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


def page_image(data, page_number, *, is_image=False):
    if is_image:
        if page_number != 1:
            raise ValueError("원본 쪽 번호를 확인하세요.")
        return image_preview(data)
    with PDF_LOCK, open_pdf(data) as document:
        if not 1 <= page_number <= len(document):
            raise ValueError("PDF 쪽 번호를 확인하세요.")
        with closing(document[page_number - 1]) as page:
            return render(page)


def recognize(image):
    if sys.platform != "win32":
        return "", "스캔 OCR은 Windows의 한국어 OCR 언어팩이 필요합니다."
    with tempfile.TemporaryDirectory(prefix="epms-ocr-") as folder:
        file = Path(folder) / "page.png"
        file.write_bytes(image)
        try:
            result = subprocess.run(
                ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                 "-File", str(SCRIPT), str(file)],
                capture_output=True, encoding="utf-8", errors="replace", timeout=15,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        except (OSError, subprocess.TimeoutExpired):
            return "", "Windows OCR을 실행하지 못했습니다. 원본을 보고 입력하세요."
    if result.returncode:
        return "", "Windows OCR을 사용할 수 없습니다. 한국어 OCR 언어팩을 확인하세요."
    return result.stdout.strip(), ""


def extract(data, *, is_image=False):
    if is_image:
        text, warning = recognize(image_preview(data))
        text = text[:32000]
        return [dict(page=1, text=text, method="Windows OCR", warning=warning, rows=candidates(text))]
    pages = []
    with PDF_LOCK, open_pdf(data) as document:
        if not 1 <= len(document) <= MAX_PAGES:
            raise ValueError(f"PDF는 1~{MAX_PAGES}쪽이어야 합니다.")
        for index in range(len(document)):
            with closing(document[index]) as page, closing(page.get_textpage()) as layer:
                text = layer.get_text_bounded()[:32000]
                method, warning = "PDF 텍스트", ""
                rows = candidates(text)
                if not any(row.get("number") or row.get("line") for row in rows):
                    recognized, warning = recognize(render(page))
                    if recognized:
                        text, method = recognized[:32000], "Windows OCR"
                        rows = candidates(text)
                pages.append(dict(page=index + 1, text=text, method=method, warning=warning, rows=rows))
    return pages
