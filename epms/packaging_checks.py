"""Inspect Windows build inputs and record provenance for a future package."""
import argparse
import importlib.metadata as metadata
import json
import platform
import struct
import sys
from pathlib import Path

from packaging.requirements import Requirement

from epms.ocr import EXTRACTION_VERSION, unipass_template
from epms.oss_inventory import ROOT, sha256


REQUIRED_SOURCE = (
    "EPMS.spec", "launch.py", "epms/windows_ocr.ps1", "epms/ocr_unipass.json",
    "epms/migrations/env.py", "docs/OCR_REVIEW_WORKFLOW.md", "docs/OPEN_SOURCE_COMPLIANCE.md",
    "docs/OCR_RELEASE_NOTES.md",
)


def pe_is_x64(stream):
    if stream.read(2) != b"MZ":
        return False
    stream.seek(0x3C)
    offset = stream.read(4)
    if len(offset) != 4:
        return False
    stream.seek(int.from_bytes(offset, "little"))
    return stream.read(6) == b"PE\0\0\x64\x86"


def preflight(variant, webview):
    if sys.platform != "win32" or struct.calcsize("P") != 8 or platform.machine().lower() not in {"amd64", "x86_64"}:
        raise ValueError("EPMS Windows x64 packaging requires x64 Windows Python.")
    missing = [name for name in REQUIRED_SOURCE if not (ROOT / name).is_file()]
    if missing or not list((ROOT / "samples").glob("2027*.xlsx")):
        raise ValueError(f"Missing packaging inputs: {', '.join(missing) or 'official workbook template'}.")
    versions = {}
    for filename in ("requirements.txt", "requirements-dev.txt"):
        for line in (ROOT / filename).read_text(encoding="utf-8").splitlines():
            if not line.strip() or line.startswith("-"):
                continue
            requirement = Requirement(line)
            if requirement.marker is not None and not requirement.marker.evaluate():
                continue
            installed = metadata.version(requirement.name)
            if installed not in requirement.specifier:
                raise ValueError(f"Installed package {requirement.name} {installed} does not match {requirement}.")
            versions[requirement.name] = installed
    runtime = {}
    if variant == "portable":
        executable = webview / "msedgewebview2.exe"
        with executable.open("rb") as content:
            if not pe_is_x64(content):
                raise ValueError("The fixed WebView2 runtime is not x64.")
        manifests = list(webview.glob("*.manifest"))
        if len(manifests) != 1:
            raise ValueError("The fixed WebView2 runtime manifest is missing or ambiguous.")
        runtime = {"version": manifests[0].stem, "sha256": sha256(executable)}
    migrations = sorted(path.name for path in (ROOT / "epms/migrations/versions").glob("*.py"))
    template = unipass_template()
    package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    lock = json.loads((ROOT / "package-lock.json").read_text(encoding="utf-8"))
    if not package.get("version") or lock.get("version") != package["version"]:
        raise ValueError("The package.json and package-lock.json release versions disagree.")
    return {"version": package["version"], "variant": variant, "architecture": "x64", "python": platform.python_version(),
            "sorting": {"feature_default": True, "operation_mode": "SETTLEMENT_REFERENCE", "operational_approval": False},
            "pyinstaller": versions["pyinstaller"], "webview2": runtime, "migrations": migrations,
            "ocr_template": {"id": template["id"], "version": template["version"]},
            "ocr_extraction_version": EXTRACTION_VERSION,
            "inputs": {name: sha256(ROOT / name) for name in (
                "requirements.txt", "requirements-dev.txt", "package-lock.json", "epms/ocr_unipass.json")}}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("variant", choices=["lite", "portable"])
    parser.add_argument("--webview", type=Path, default=ROOT / "build/WebView2")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = json.dumps(preflight(args.variant, args.webview), indent=2) + "\n"
    if args.output:
        args.output.write_text(result, encoding="utf-8")
    else:
        print(result, end="")
