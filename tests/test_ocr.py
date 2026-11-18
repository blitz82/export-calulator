from contextlib import closing
from io import BytesIO

import pytest
from PIL import Image

from epms import ocr, ocr_profiles


def searchable_pdf():
    stream = b"BT /F1 18 Tf 66 99 Td (DECL-12345) Tj ET"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Count 1 /Kids [3 0 R] >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 200] "
        b"/CropBox [30 35 230 155] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream),
    ]
    data = bytearray(b"%PDF-1.4\n")
    offsets = []
    for index, body in enumerate(objects, 1):
        offsets.append(len(data))
        data.extend(b"%d 0 obj\n%s\nendobj\n" % (index, body))
    xref = len(data)
    data.extend(b"xref\n0 6\n0000000000 65535 f \n")
    for offset in offsets:
        data.extend(b"%010d 00000 n \n" % offset)
    data.extend(b"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % xref)
    return bytes(data)


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_pdf_evidence_follows_rendered_rotation_and_cropbox(monkeypatch, rotation):
    def rows(text, locate, method):
        assert text.strip() == "DECL-12345"
        box, = locate("DECL-12345")
        source = {"number": {"box": box, "method": method}}
        return [({"line": "001"}, dict(source)), ({"line": "002"}, dict(source))]

    monkeypatch.setattr(ocr, "unipass_rows", rows)
    with ocr.open_pdf(searchable_pdf()) as doc, closing(doc[0]) as page:
        page.set_rotation(rotation)
        _, results = ocr.read_pdf_page(page)
        assert page.get_rotation() == rotation
        assert page.get_bbox() == (30, 35, 230, 155)
        first, second = [source["number"]["box"] for _, source in results]
        assert first == second
        for scale in (1, 2.5):
            with closing(page.render(scale=scale)) as image:
                dark = image.to_pil().convert("L").point(lambda pixel: 255 if pixel < 150 else 0).getbbox()
                bounds = [coord * size for coord, size in zip(
                    first, (image.width, image.height, image.width, image.height))]
                assert bounds == pytest.approx(dark, abs=2 * scale)


def test_region_retry_crops_the_rotated_preview(monkeypatch):
    def inspect_crop(image):
        with Image.open(BytesIO(image)) as crop:
            width, height = crop.size
            dark = crop.convert("L").point(lambda pixel: 255 if pixel < 150 else 0).getbbox()
            assert height > width
            assert dark[2] - dark[0] > width / 2
            assert dark[3] - dark[1] > height / 2
        return {"lines": [{"text": "DECL-12345"}]}, ""

    monkeypatch.setattr(ocr, "recognize", inspect_crop)
    with ocr.open_pdf(searchable_pdf()) as doc, closing(doc[0]) as page:
        page.set_rotation(270)
        with closing(page.get_textpage()) as layer:
            canon, = ocr.pdf_locator(layer, page)("DECL-12345")
        buffer = BytesIO()
        doc.save(buffer)
    result = ocr.recognize_region(buffer.getvalue(), 1, ocr.preview_box(canon, 270))
    assert result == {"text": "DECL-12345", "warning": ""}


@pytest.mark.parametrize(("heading", "variant"), [
    ("수출신고필증(적재전, 갑지)", "preloading-cover"),
    ("수출신고필증(적 재 전, 을지)", "preloading-continuation"),
    ("수출신고필증(수출이행, 갑지)", "completed-cover"),
    ("수출이행신고필증(수출이행, 을지)", "completed-continuation"),
    ("수출신고서(적재전, 갑지)", None),
])
def test_extracted_page_identifies_template_variant(heading, variant):
    info = ocr.extracted_page(1, heading, "PDF", "", [])["template"]
    assert (info["variant"] if info else None) == variant


@pytest.mark.parametrize(("field", "source", "expected"), [
    ("line", "003 / 012", "003"),
    ("line", "2 / 12", "002"),
    ("version", "02", "02"),
    ("country", "대한민국", "대한민국"),
    ("product", "신선 포도", "신선 포도"),
    ("quantity", "1,200.50", "1200.50"),
    ("quantity", "12 34", None),
    ("unit", "PCS", "PCS"),
    ("agent", "123-45-67834", "1234567834"),
    ("owner", "123-45-67835", None),
    ("maker", "123-45-67834\n123-45-67835", None),
    ("owner_name", "(주) 수출상사", "(주) 수출상사"),
    ("maker_name", "제조사\n다른 업체", None),
])
def test_ocr_region_accepts_only_the_expected_value_shape(field, source, expected):
    assert ocr_profiles.selection_values(source).get(field) == expected


def test_unipass_repeated_lines_exclude_document_total():
    text = """수출신고필증(적재전, 갑지)
신고번호   신고일자
12345-26-012345X  2026-01-02
품명 규격 (란번호/총란수: 001/002)
세번부호 1234.56-7890 순중량 10(KG) 신고가격(FOB)
$ 80
품명 규격 (란번호/총란수: 002/002)
세번부호 2345.67-8901 순중량 20(KG) 신고가격(FOB)
$ 90
총중량 30 KG   총신고가격(FOB) $ 170
"""
    boxes = {
        "신고번호": [(0.20, 0.08, 0.24, 0.09)],
        "신고일자": [(0.60, 0.08, 0.64, 0.09)],
        "12345-26-012345X": [(0.20, 0.10, 0.32, 0.11)],
        "2026-01-02": [(0.60, 0.10, 0.68, 0.11)],
    }
    rows = ocr.unipass_rows(text, lambda value: boxes.get(value, []), "PDF")
    assert [(row["line"], row["hs"], row["kg"], row["usd"]) for row, _ in rows] == [
        ("001", "1234.56-7890", "10", "80"), ("002", "2345.67-8901", "20", "90")
    ]
    assert rows[0][0]["number"] == "12345-26-012345X"
    assert rows[0][1]["declared"]["box"] == boxes["2026-01-02"][0]
    assert ocr.unipass_rows(text.replace("신고필증", "신고서"), lambda _: [], "PDF") == []


def test_ocr_merge_preserves_primary_and_provenance():
    primary = [({"number": "test", "line": "002", "usd": "10"}, {})]
    secondary = [
        ({"number": "test", "line": "001", "kg": "99"}, {}),
        ({"number": "test", "line": "002", "usd": "11", "kg": "5"}, {"kg": {"method": "Windows OCR"}}),
    ]
    (row, source), = ocr.merge_rows(primary, secondary)
    assert row["usd"] == row["fob"] == "10"
    assert row["kg"] == "5"
    assert source["kg"]["method"] == "Windows OCR"
    assert source["usd"]["alternate"] == "11"


def test_unreadable_item_number_keeps_fields_in_their_own_blocks():
    text = """수출신고필증(적재전, 갑지)
신고번호 12345-26-012345X
품명 규격 (란번호/총란수: ?/002)
세번부호 1234.56-7890 순중량 10(KG) 신고가격(FOB) $80
품명 규격 (란번호/총란수: 002/002)
세번부호 2345.67-8901 순중량 20(KG) 신고가격(FOB) $90
총중량 30(KG) 총신고가격(FOB) $170
"""
    rows = ocr.unipass_rows(text, lambda _: [], "PDF")
    assert [(row.get("line"), row["hs"], row["kg"], row["usd"]) for row, _ in rows] == [
        (None, "1234.56-7890", "10", "80"), ("002", "2345.67-8901", "20", "90")
    ]
    assert {row["number"] for row, _ in rows} == {"12345-26-012345X"}
    assert "line" not in rows[0][1]
    assert rows[1][1]["line"]["raw"] == "002"
    assert "box" not in rows[1][1]["line"]


def test_windows_coordinates_use_preview_scale_and_skip_skew():
    payload = {"width": 1000, "height": 500, "angle": 0,
               "lines": [{"words": [{"text": "신고", "rect": [100, 55, 20, 10]},
                                     {"text": "번호", "rect": [125, 55, 20, 10]}]}]}
    assert ocr.windows_locator(payload)("신고번호") == [(0.1, 0.11, 0.145, 0.13)]
    payload["angle"] = 90
    assert ocr.windows_locator(payload)("신고번호") == []


def test_hybrid_merge_compares_formats_and_preserves_conflict_evidence():
    alternate = {"raw": "15.2", "box": (.1, .2, .3, .4)}
    primary = [({"number": "12345-26-012345X", "line": "001", "usd": "1,000.00",
                 "declared": "2026/01/01", "kg": "15.1"}, {"kg": {"raw": "15.1"}})]
    secondary = [
        ({"number": "1234526012345x", "line": "001", "usd": "1000",
          "declared": "2026-01-01", "kg": "15.2", "hs": "1234.56-7890"}, {"kg": alternate}),
        ({"number": "1234526012345x", "line": "1", "kg": "99"}, {}),
    ]
    (row, evidence), = ocr.merge_rows(primary, secondary)
    assert row["usd"] == "1,000.00"
    assert row["kg"] == "15.1"
    assert row["hs"] == "1234.56-7890"
    assert "usd" not in evidence and "declared" not in evidence
    assert evidence["kg"] == {"raw": "15.1", "status": "CONFLICT", "alternate": "15.2",
                              "alternate_evidence": alternate}


def test_scan_retries_with_contrast_after_empty_result(monkeypatch):
    output = BytesIO()
    with Image.new("RGB", (12, 8), (100, 100, 100)) as original:
        original.putpixel((1, 1), (130, 130, 130))
        original.save(output, format="PNG")
    calls = []

    def recognize(image):
        with Image.open(BytesIO(image)) as bitmap:
            calls.append(bitmap.mode)
            assert bitmap.size == (12, 8)
            if bitmap.mode == "RGB":
                return {"width": 12, "height": 8, "lines": []}, ""
            assert bitmap.getextrema() == (0, 255)
        return {"width": 12, "height": 8, "lines": [
            {"text": "수출신고필증(적재전, 갑지)", "words": []},
            {"text": "신고번호 12345-26-012345X", "words": []},
        ]}, ""

    monkeypatch.setattr(ocr, "recognize", recognize)
    _, rows, warning = ocr.read_image(output.getvalue(), enhance=True)
    assert warning == ""
    assert calls == ["RGB", "L"]
    assert rows[0][0]["number"] == "12345-26-012345X"


def test_pdf_image_layer_triggers_scan_fallback_and_keeps_preview_coordinates(monkeypatch):
    with ocr.open_pdf(searchable_pdf()) as document, closing(document[0]) as page:
        page.set_rotation(90)
        output = BytesIO()
        document.save(output)
    monkeypatch.setattr(ocr, "has_scan_image", lambda _: True)

    def scan(image, *, enhance):
        assert enhance
        with Image.open(BytesIO(image)) as bitmap:
            assert bitmap.width > bitmap.height
        return "수출신고필증(적재전, 갑지)", [
            ({"line": "001"}, {"line": {"box": (0.1, 0.2, 0.3, 0.4)}})
        ], ""

    monkeypatch.setattr(ocr, "read_image", scan)
    page, = ocr.extract(output.getvalue())
    assert page["method"] == "Windows OCR"
    assert page["evidence"][0]["line"]["box"] == pytest.approx((0.6, 0.1, 0.8, 0.3))


@pytest.mark.parametrize("missing_field", [False, True])
def test_pdf_core_fields_control_ocr_fallback(monkeypatch, missing_field):
    fields = {"number": "12345-26-012345X", "declared": "2026-01-01",
              "line": "001", "hs": "1234.56-7890", "usd": "80"}
    if not missing_field:
        fields["kg"] = "5"
    monkeypatch.setattr(ocr, "read_pdf_page", lambda _: (
        "수출신고필증(적재전, 갑지)", [(fields, {"usd": {"raw": "80"}})]))
    calls = []

    def scan(image, *, enhance):
        assert not enhance
        calls.append(image)
        return "", [({"number": fields["number"], "line": fields["line"], "usd": "90", "kg": "5"},
                     {"kg": {"raw": "5", "method": "Windows OCR"}})], ""

    monkeypatch.setattr(ocr, "read_image", scan)
    page, = ocr.extract(searchable_pdf())
    assert bool(calls) == missing_field
    assert page["rows"][0]["usd"] == "80" and page["rows"][0]["kg"] == "5"
    if missing_field:
        assert page["evidence"][0]["usd"]["alternate"] == "90"
        assert page["evidence"][0]["kg"]["method"] == "Windows OCR"


def test_explicit_optional_anchor_can_trigger_ocr_with_complete_core_fields():
    rows = [({key: "candidate" for key in ocr.CORE_FIELDS}, {})]
    assert ocr.needs_ocr("신고수리일자: ?", rows)
    assert not ocr.needs_ocr("출항예정일자: ?", rows)


def test_scan_reorders_header_and_joins_item_columns_without_using_totals():
    payload = {"width": 1000, "height": 1000, "angle": 0, "lines": [
        {"words": [{"text": text, "rect": [x, y, 45, 10]}]} for text, x, y in [
            ("잡문자", 20, 300), ("순중량", 300, 510), ("25.50", 395, 511),
            ("(KG)", 450, 511), ("세번부호", 90, 502), ("1234.56-7890", 170, 502),
            ("신고가격(흐림)", 680, 510), ("$1,200.50", 850, 503),
            ("총신고가격", 680, 603), ("$25,900.20", 850, 603),
            ("수출신고필증(적재전,", 320, 100), ("갑지)", 650, 100),
        ]
    ]}
    payload["lines"] = ocr.spatial_ocr_lines(payload)
    text = "\n".join(line["text"] for line in payload["lines"])
    (values, evidence), = ocr.unipass_rows(text, ocr.windows_locator(payload), "Windows OCR")
    assert (values.get("line"), values["hs"], values["kg"], values["usd"]) == (
        None, "1234.56-7890", "25.50", "1,200.50")
    assert evidence["usd"]["box"][1] == pytest.approx(.503)


def test_scan_crop_recovers_leading_zero_with_label_and_page_box(monkeypatch):
    output = BytesIO()
    Image.new("RGB", (100, 100), "white").save(output, format="PNG")
    monkeypatch.setattr(ocr, "recognize", lambda _: ({"width": 100, "height": 30, "lines": [
        {"text": "란번호/종란수 002/005", "words": [
            {"text": "란번호/종란수", "rect": [6, 5, 40, 10]},
            {"text": "002/005", "rect": [50, 5, 20, 10]},
        ]},
    ]}, ""))
    values, evidence = {}, {}
    ocr.retry_item_number(output.getvalue(), lambda _: [(.2, .3, .3, .31)], values, evidence)
    assert values == {"line": "002"}
    assert evidence["line"]["raw"] == "002/005"
    assert evidence["line"]["box"][0] > .2


def test_continuation_preserves_leading_zero_and_tracks_value_page():
    number = "12345-26-012345X"
    cover = ocr.extracted_page(1, "수출신고필증(적재전, 갑지)", "PDF", "", [
        ({"number": number, "line": "001", "hs": "1234.56-7890", "version": "02"}, {}),
        ({"number": number, "line": "002", "usd": "48"}, {}),
    ])
    # Identified pages should have one revision across their candidate rows.
    cover["rows"][1]["version"] = "02"
    following = ocr.extracted_page(2, "수출신고필증(적재전, 을지)\n총중량 25 (KG) 총신고가격(FOB) $ 100",
                                    "PDF", "", [
        ({"number": number, "line": "001", "kg": "15.1", "usd": "52", "fob": "52", "version": "02"},
         {"kg": {"box": (.30, .55, .39, .57), "raw": "15.1"}}),
        ({"number": number, "line": "002", "usd": "48.00", "version": "02"}, {}),
    ])
    first, second = ocr.stitch_continuations([cover, following])
    assert [(r["line"], r["usd"]) for r in first["rows"]] == [("001", "52"), ("002", "48")]
    assert first["evidence"][0]["kg"]["page"] == 2
    assert second["rows"] == []
    assert second["document_totals"] == {"total_kg": "25", "fob_usd": "100"}


@pytest.mark.parametrize(("second_title", "number", "version", "kg", "gap"), [
    ("수출신고필증(적재전, 갑지)", "12345-26-012345X", "2", "12", False),
    ("수출신고필증(적재전, 을지)", "12345-26-012347X", "2", "12", False),
    ("수출신고필증(적재전, 을지)", "12345-26-012345X", "3", "12", False),
    ("수출신고필증(적재전, 을지)", "12345-26-012345X", "2", "99", False),
    ("수출신고필증(적재전, 을지)", "12345-26-012345X", "2", "12", True),
])
def test_continuation_does_not_combine_different_or_conflicting_rows(second_title, number, version, kg, gap):
    first = ocr.extracted_page(1, "수출신고필증(적재전, 갑지)", "PDF", "", [
        ({"number": "12345-26-012345X", "line": "001", "kg": "12", "version": "2"}, {})
    ])
    second = ocr.extracted_page(3 if gap else 2, second_title, "PDF", "", [
        ({"number": number, "line": "001", "kg": kg, "version": version, "usd": "99"}, {})
    ])
    between = [ocr.extracted_page(2, "Air Waybill", "PDF", "", [])] if gap else []
    ocr.stitch_continuations([first, *between, second])
    assert "usd" not in first["rows"][0]
    assert second["rows"][0]["usd"] == "99"
    if kg == "99":
        assert "서로 다릅니다" in second["warning"]


@pytest.mark.parametrize("payload", [
    [], {"width": 10, "height": 20, "lines": None},
    {"width": 10, "height": 20, "lines": [{"text": "word", "words": [{"text": "word", "rect": [1, 2]}]}]},
])
def test_structurally_invalid_windows_result_is_rejected(payload):
    assert not ocr.valid_ocr_payload(payload)
