"""Measure extraction against locally supplied truth and print aggregate counts only."""
import argparse
import json
from collections import Counter, defaultdict
from contextlib import closing
from pathlib import Path
from time import perf_counter

from epms import ocr


def align(expected, actual):
    if len(expected) == len(actual) == 1:
        return [(expected[0], actual[0])]
    right = defaultdict(list)
    left = Counter(ocr.row_identity(row) for row in expected)
    for index, row in enumerate(actual):
        right[ocr.row_identity(row)].append(index)
    pairs, used = [], set()
    for row in expected:
        key = ocr.row_identity(row)
        matches = right[key]
        if all(key) and len(matches) == 1 and left[key] == 1:
            index = matches[0]
            pairs.append((row, actual[index]))
            used.add(index)
        else:
            pairs.append((row, None))
    return pairs + [(None, row) for index, row in enumerate(actual) if index not in used]


def score(truth, actual):
    fields = defaultdict(Counter)
    counts = Counter(documents=1)
    tracked = {key for rows in truth.values() for row in rows for key in row}
    for page in sorted(truth.keys() | actual.keys()):
        for expected, got in align(truth.get(page, []), actual.get(page, [])):
            if expected is None:
                counts["unexpected_items"] += 1
                for key in tracked | {"number", "line", "hs", "kg", "usd"}:
                    fields[key]["false_extra"] += bool(got.get(key))
                continue
            counts["expected_items"] += 1
            if got is None:
                counts["missing_items"] += 1
            got = got or {}
            if got and all(ocr.row_identity(expected)) and ocr.row_identity(expected) == ocr.row_identity(got):
                counts["correct_item_identity"] += 1
            for key, value in expected.items():
                result = got.get(key)
                if value is None:
                    fields[key]["false_extra"] += bool(result)
                    continue
                fields[key]["expected"] += 1
                if not result:
                    fields[key]["missing"] += 1
                elif ocr.comparison_value(key, value) == ocr.comparison_value(key, result):
                    fields[key]["exact"] += 1
                else:
                    fields[key]["wrong"] += 1
    counts["exact_documents"] = int(not counts["missing_items"] and not counts["unexpected_items"]
                                    and not any(row["wrong"] or row["missing"] or row["false_extra"] for row in fields.values()))
    return counts, fields


def rows_by_page(data, method):
    if method == "hybrid":
        return {page["page"]: page["rows"] for page in ocr.extract(data)}
    pages = []
    with ocr.PDF_LOCK, ocr.open_pdf(data) as document:
        for index in range(len(document)):
            with closing(document[index]) as page:
                if method == "windows":
                    rotation = page.get_rotation()
                    text, matches, warning = ocr.read_image(ocr.render(page, rotation=(-rotation) % 360), enhance=True)
                    if warning and not text:
                        raise RuntimeError("Windows OCR engine unavailable for benchmark.")
                    name = "Windows OCR"
                else:
                    text, matches = ocr.read_pdf_page(page)
                    warning, name = "", "PDF 텍스트"
                    if method == "legacy":
                        matches = [(row, {}) for row in ocr.candidates(text) if row]
                pages.append(ocr.extracted_page(index + 1, text, name, warning, matches))
    if method != "legacy":
        pages = ocr.stitch_continuations(pages)
    return {page["page"]: page["rows"] for page in pages}


def evaluate(path, methods):
    manifest = json.loads(path.read_text(encoding="utf-8"))
    cohorts = defaultdict(lambda: {"counts": Counter(), "fields": defaultdict(Counter), "seconds": []})
    for item in manifest["samples"]:
        data = (path.parent / item["pdf"]).read_bytes()
        truth = {int(page): rows for page, rows in item.get("truth", {}).items()}
        for method in methods:
            cohort = cohorts[(item["kind"], method)]
            start = perf_counter()
            try:
                actual = rows_by_page(data, method)
            except ValueError:
                cohort["counts"]["rejected_as_expected" if item.get("expect_rejected") else "unexpected_error"] += 1
                continue
            except RuntimeError:
                if method != "windows":
                    raise
                cohort["counts"]["engine_unavailable"] += 1
                continue
            cohort["seconds"].append(perf_counter() - start)
            if item.get("expect_rejected"):
                cohort["counts"]["unexpected_accept"] += 1
                continue
            counts, fields = score(truth, actual)
            cohort["counts"].update(counts)
            for key, row in fields.items():
                cohort["fields"][key].update(row)
    result = {}
    for (kind, method), data in sorted(cohorts.items()):
        totals = Counter()
        for row in data["fields"].values():
            totals.update(row)
        filled = totals["exact"] + totals["wrong"]
        def rate(numerator, denominator):
            return round(numerator / denominator * 100, 2) if denominator else None
        result.setdefault(kind, {})[method] = {
            "counts": dict(data["counts"]), "totals": dict(totals),
            "fields": data["fields"], "extraction_seconds": [round(value, 3) for value in data["seconds"]],
            "percent": {
                "filled": rate(filled, totals["expected"]), "exact": rate(totals["exact"], totals["expected"]),
                "wrong_among_filled": rate(totals["wrong"], filled), "missing": rate(totals["missing"], totals["expected"]),
                "exact_documents": rate(data["counts"]["exact_documents"], data["counts"]["documents"]),
                "correct_item_identity": rate(data["counts"]["correct_item_identity"], data["counts"]["expected_items"]),
            },
        }
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--methods", nargs="+", choices=["legacy", "direct", "windows", "hybrid"],
                        default=["legacy", "direct", "hybrid"])
    args = parser.parse_args()
    print(json.dumps(evaluate(args.manifest, args.methods), ensure_ascii=False, indent=2))
