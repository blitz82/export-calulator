"""Measure the direct PDF extraction path without recording source text or values."""
import argparse
import json
from collections import Counter
from contextlib import closing
from hashlib import file_digest
from pathlib import Path
from time import perf_counter

from epms import ocr


def audit(folder):
    files = sorted(folder.glob("*.pdf"))
    if not files:
        raise ValueError("No PDF files in the sample directory.")
    summary, legacy, direct, evidence = Counter(), Counter(), Counter(), Counter()
    variants = Counter()
    seconds = {"legacy_parser": 0.0, "direct_pdf_extraction": 0.0}
    samples = []
    for sample_id, file in enumerate(files, 1):
        with file.open("rb") as handle:
            digest = file_digest(handle, "sha256").hexdigest()
        record = {"sample_id": sample_id, "sha256": digest, "pages": []}
        samples.append(record)
        with ocr.open_pdf(file.read_bytes()) as document:
            for index in range(len(document)):
                with closing(document[index]) as page:
                    start = perf_counter()
                    raw, matches = ocr.read_pdf_page(page)
                    seconds["direct_pdf_extraction"] += perf_counter() - start
                    item = {"page": index + 1, "size_pt": list(page.get_size()),
                            "rotation_degrees": page.get_rotation(), "text_characters": len(raw.strip()),
                            "certificate_heading": False, "candidates": []}
                    record["pages"].append(item)
                    summary["pages"] += 1
                    if not raw.strip():
                        summary["without_text"] += 1
                        continue
                    summary["with_text"] += 1
                    start = perf_counter()
                    previous = ocr.candidates(raw)
                    seconds["legacy_parser"] += perf_counter() - start
                    if not matches:
                        continue
                    item["certificate_heading"] = True
                    variant = ocr.certificate_variant(ocr.page_heading(raw), ocr.unipass_template())
                    item["template_variant"] = variant
                    variants[variant] += 1
                    summary["recognized_heading"] += 1
                    for row in previous:
                        legacy.update(row.keys())
                    for row, locations in matches:
                        direct.update(row.keys())
                        evidence.update(field for field, data in locations.items() if "box" in data)
                        item["candidates"].append({"fields": sorted(row), "boxes": {
                            field: data["box"] for field, data in locations.items() if "box" in data}})
    return {"summary": {"files": len(samples), **summary}, "template_variants": dict(variants),
            "legacy_fields": dict(legacy),
            "direct_fields": dict(direct), "evidence_fields": dict(evidence),
            "seconds": {method: round(elapsed, 3) for method, elapsed in seconds.items()}}, samples


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder", type=Path)
    parser.add_argument("--private-manifest", type=Path,
                        help="Optional local JSON for file hashes and page metrics. Keep outside source control.")
    options = parser.parse_args()
    totals, details = audit(options.folder)
    if options.private_manifest:
        with options.private_manifest.open("x", encoding="utf-8") as output:
            json.dump(details, output, indent=2)
    print(json.dumps(totals, indent=2))
