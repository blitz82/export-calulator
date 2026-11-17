"""Generate fictional, independently specified OCR cases in a development directory."""
import argparse
import json
import os
from contextlib import closing
from io import BytesIO
from pathlib import Path

import pypdfium2 as pdfium
from reportlab.lib.pdfencrypt import StandardEncryption
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas


NUMBER = "12345-26-012345X"
BASE = {"number": NUMBER, "declared": "2026-11-17", "line": "001", "hs": "0806.10-0000",
        "kg": "18", "usd": "120", "serial": "007", "accepted": "2026-11-18", "shipped": None}


def certificate(canvas, *, sheet="갑지", line="001", total="001", version="", fields=("hs", "kg", "usd", "serial", "accepted"),
                hs="0806.10-0000", kg="18", usd="120"):
    canvas.setFont("BenchmarkKorean", 10)
    labels = [(160, 755, f"수출신고필증(적재전, {sheet})"), (80, 720, "신고번호"),
              (276, 720, "신고일자"), (80, 706, NUMBER), (276, 706, "2026-11-17"),
              (65, 500, f"품명 규격 (란번호/총란수: {line}/{total})")]
    if version:
        labels.append((130, 690, f"정정 차수 {version}"))
    item = {"hs": (65, 430, f"세번부호 {hs}"), "kg": (195, 430, f"순중량 {kg} (KG)"),
            "usd": (360, 430, f"신고가격(FOB) ${usd}"), "serial": (65, 480, "일련번호 007"),
            "accepted": (425, 80, "신고수리일자 2026-11-18")}
    labels.extend(item[key] for key in fields)
    labels.extend([(65, 360, "총중량 250 (KG)"), (345, 360, "총신고가격(FOB) $ 999")])
    for x, y, text in labels:
        canvas.drawString(x, y, text)
    canvas.showPage()


def vector(*pages, encrypted=False):
    out = BytesIO()
    canvas = Canvas(out, pagesize=(600, 800), encrypt=StandardEncryption("fixture-only") if encrypted else None)
    for page in pages:
        certificate(canvas, **page)
    canvas.save()
    return out.getvalue()


def raster(vector_data, scale, *, rotation=0, attachment=False):
    out = BytesIO()
    with pdfium.PdfDocument(vector_data) as doc, closing(doc[0]) as page, closing(page.render(scale=scale)) as bitmap:
        canvas = Canvas(out, pagesize=(600, 800))
        canvas.setPageRotation(rotation)
        canvas.drawImage(ImageReader(bitmap.to_pil()), 0, 0, 600, 800)
        canvas.showPage()
        if attachment:
            canvas.setPageRotation(0)
            canvas.drawString(50, 730, "AIR WAYBILL - SYNTHETIC")
            canvas.showPage()
        canvas.save()
    return out.getvalue()


def generate(directory, font):
    pdfmetrics.registerFont(TTFont("BenchmarkKorean", str(font)))
    directory.mkdir(parents=True, exist_ok=True)
    manifest = {"samples": []}

    def add(name, kind, data, truth=None, rejected=False):
        (directory / name).write_bytes(data)
        manifest["samples"].append({"pdf": name, "kind": kind, "truth": truth or {}, "expect_rejected": rejected})

    clean = vector({})
    add("electronic.pdf", "electronic", clean, {1: [BASE]})
    partial = {key: BASE[key] for key in ("number", "line", "declared", "hs", "serial")}
    partial.update(kg=None, usd=None)
    add("partial.pdf", "electronic-partial", vector({"fields": ("hs", "serial")}), {1: [partial]})
    add("revision.pdf", "electronic-revision-continuation",
        vector({"version": "02", "fields": ("hs", "serial")}, {"sheet": "을지", "version": "02", "fields": ("kg", "usd", "accepted")}),
        {1: [BASE | {"version": "02"}]})
    add("distinct-lines.pdf", "electronic-multiple-items",
        vector({"total": "002"}, {"sheet": "을지", "line": "002", "total": "002", "hs": "0805.21-0000", "kg": "22", "usd": "80"}),
        {1: [BASE], 2: [BASE | {"line": "002", "hs": "0805.21-0000", "kg": "22", "usd": "80"}]})
    add("scan.pdf", "scan", raster(clean, 2.5), {1: [BASE]})
    add("scan-low.pdf", "scan-low-resolution", raster(clean, .8), {1: [BASE]})
    add("scan-mixed.pdf", "scan-mixed", raster(clean, 2.5, attachment=True), {1: [BASE], 2: []})
    with pdfium.PdfDocument(raster(clean, 2.5)) as doc, closing(doc[0]) as page:
        page.set_rotation(90)
        out = BytesIO()
        doc.save(out)
    add("scan-rotated.pdf", "scan-rotated", out.getvalue(), {1: [BASE]})
    add("damaged.pdf", "negative", b"%PDF-1.4\nbroken", rejected=True)
    add("encrypted.pdf", "negative", vector({}, encrypted=True), rejected=True)
    (directory / "truth.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    parser.add_argument("--font", type=Path, default=Path(os.getenv("WINDIR", "C:/Windows")) / "Fonts" / "malgun.ttf")
    args = parser.parse_args()
    generate(args.directory, args.font)
