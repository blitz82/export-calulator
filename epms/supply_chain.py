"""Read-only ZIP verification and explicit vulnerability lookup for SBOM package IDs."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
from urllib.error import URLError
from urllib.parse import unquote
from urllib.request import Request, urlopen
from zipfile import ZipFile

from epms.oss_inventory import sha256
from epms.packaging_checks import pe_is_x64


BINARY_TYPES = {".dll", ".exe", ".pyd", ".js"}
PRIVATE_TYPES = {".pdf", ".db", ".sqlite", ".sqlite3", ".db-wal", ".db-shm", ".sqlite-wal", ".sqlite-shm"}


def verify_graph(bom):
    refs = [component.get("bom-ref") for component in bom["components"]]
    root = bom["metadata"]["component"].get("bom-ref")
    if not root or any(not ref for ref in refs) or len(set([root, *refs])) != len(refs) + 1:
        raise ValueError("A graph requires unique populated component references.")
    dependency_refs = [edge["ref"] for edge in bom["dependencies"]]
    if root not in dependency_refs or len(set(dependency_refs)) != len(dependency_refs):
        raise ValueError("The dependency graph is missing its root or repeats dependency entries.")
    for dependency in bom["dependencies"]:
        if {dependency["ref"], *dependency.get("dependsOn", [])} - {root, *refs}:
            raise ValueError("The dependency graph contains an unknown component reference.")
    return {"components": len(refs), "dependency_records": len(bom["dependencies"]),
            "dependency_edges": sum(len(edge.get("dependsOn", [])) for edge in bom["dependencies"])}


def notice_status(text, entries):
    paths = {line.strip() for line in text.splitlines() if line.startswith("  licenses/")}
    missing = paths - entries
    if missing:
        raise ValueError("An original license file referenced in the notices is missing.")
    return {"referenced_license_files": len(paths), "license_text_pending": text.count("License text pending"),
            "license_identifier_pending": text.count("license identifier pending")}


def verify_layout(archive, names, prefix, variant, require_build_info):
    required = {
        "_internal/dist/index.html", "_internal/epms/windows_ocr.ps1", "_internal/epms/ocr_unipass.json",
        "_internal/epms/migrations/env.py", "_internal/pypdfium2_raw/pdfium.dll",
        "_internal/samples/2027-공식양식.xlsx", "실행 안내.txt", "OCR 안내.md",
    }
    contents = {name.removeprefix(prefix + "/") for name in names if name.startswith(prefix + "/")}
    if require_build_info:
        required |= {"build-info.json", "RELEASE_NOTES.md", "_internal/dist/bundle-components.json"}
        if variant == "portable":
            required.add("licenses/runtime/WebView2-Fixed-Version-LICENSE.html")
    if required - contents:
        raise ValueError(f"Missing packaged resources: {', '.join(sorted(required - contents))}")
    spreadsheets = {name for name in contents if PurePosixPath(name).suffix.lower() in {".xlsx", ".xls", ".xlsm", ".csv"}}
    if spreadsheets != {"_internal/samples/2027-공식양식.xlsx"}:
        raise ValueError("Unexpected spreadsheet in ZIP.")
    executables = [name for name in contents if "/" not in name and name.lower().endswith(".exe")]
    if len(executables) != 1:
        raise ValueError("Expected exactly one application executable.")
    with archive.open(prefix + "/" + executables[0]) as stream:
        if not pe_is_x64(stream):
            raise ValueError("Packaged application is not Windows x64.")
    migrations = sorted(PurePosixPath(name).name for name in contents
                        if name.startswith("_internal/epms/migrations/versions/") and name.endswith(".py"))
    if require_build_info:
        info = json.loads(archive.read(prefix + "/build-info.json"))
        if info.get("variant") != variant or info.get("architecture") != "x64" or info.get("migrations") != migrations:
            raise ValueError("Packaged resources disagree with build information.")
        raw_template = archive.read(prefix + "/_internal/epms/ocr_unipass.json")
        template = json.loads(raw_template)
        if info.get("ocr_template") != {"id": template["id"], "version": template["version"]}:
            raise ValueError("Packaged OCR template version disagrees with build information.")
        if info["inputs"]["epms/ocr_unipass.json"] != hashlib.sha256(raw_template).hexdigest():
            raise ValueError("Packaged OCR template hash disagrees with build information.")
        frontend = json.loads(archive.read(prefix + "/_internal/dist/bundle-components.json"))
        if info["inputs"]["package-lock.json"] != frontend["lock_sha256"]:
            raise ValueError("Packaged frontend lockfile hash disagrees with build information.")
        if variant == "portable":
            with archive.open(prefix + "/WebView2/msedgewebview2.exe") as stream:
                if not pe_is_x64(stream):
                    raise ValueError("Packaged WebView2 runtime is not Windows x64.")
                stream.seek(0)
                if hashlib.file_digest(stream, "sha256").hexdigest() != info["webview2"]["sha256"]:
                    raise ValueError("WebView2 runtime bytes disagree with build information.")
    return {"architecture": "x64", "migrations": migrations, "build_info": "build-info.json" in contents}


def verify_zip(path, variant, bom_path, *, require_build_info=False):
    expected = path.with_suffix(path.suffix + ".sha256").read_text(encoding="ascii").strip().lower()
    if sha256(path) != expected:
        raise ValueError("ZIP SHA-256 does not match the sidecar.")
    with ZipFile(path) as archive:
        names = [entry.filename for entry in archive.infolist() if not entry.is_dir()]
        if len(set(name.casefold() for name in names)) != len(names):
            raise ValueError("Duplicate ZIP paths.")
        for name in names:
            entry = PurePosixPath(name)
            if entry.is_absolute() or ".." in entry.parts or "\\" in name or ":" in name:
                raise ValueError("Unsafe path in ZIP.")
            if entry.suffix.lower() in PRIVATE_TYPES:
                raise ValueError("Unexpected document or database in ZIP.")
        matches = [name for name in names if name.endswith(f"/sbom/epms-{variant}.cdx.json")]
        if len(matches) != 1:
            raise ValueError("Expected embedded SBOM was not found.")
        prefix = matches[0].split("/sbom/")[0]
        raw = archive.read(matches[0])
        if raw != bom_path.read_bytes():
            raise ValueError("Embedded SBOM differs from the supplied SBOM.")
        bom = json.loads(raw)
        if require_build_info:
            verify_graph(bom)
        binaries = {name.removeprefix(prefix + "/") for name in names
                    if name.startswith(prefix + "/") and PurePosixPath(name).suffix.lower() in BINARY_TYPES}
        records = {item["name"]: item for item in bom["components"] if item["type"] == "file"}
        if records.keys() != binaries:
            raise ValueError("SBOM paths and ZIP binary/JavaScript paths do not match.")
        for name, record in records.items():
            digest = next(item["content"] for item in record["hashes"] if item["alg"] == "SHA-256")
            with archive.open(prefix + "/" + name) as stream:
                if hashlib.file_digest(stream, "sha256").hexdigest() != digest:
                    raise ValueError("A packaged file differs from its SBOM SHA-256.")
        required = {"THIRD_PARTY_NOTICES.txt", "licenses/index.html"}
        if not all(prefix + "/" + entry in names for entry in required):
            raise ValueError("A packaged notice is missing.")
        notices = notice_status(archive.read(prefix + "/THIRD_PARTY_NOTICES.txt").decode("utf-8"),
                                {name.removeprefix(prefix + "/") for name in names if name.startswith(prefix + "/")})
        webview = prefix + "/WebView2/msedgewebview2.exe" in names
        if webview != (variant == "portable"):
            raise ValueError("WebView2 runtime does not match the ZIP variant.")
        layout = verify_layout(archive, names, prefix, variant, require_build_info)
        runtime = {}
        pdfium_path = prefix + "/_internal/pypdfium2_raw/version.json"
        if pdfium_path in names:
            pdfium = json.loads(archive.read(pdfium_path))
            runtime["pdfium"] = ".".join(str(pdfium[key]) for key in ("major", "minor", "build", "patch"))
        for item in bom["components"]:
            if item["name"] in {"CPython", "Microsoft Edge WebView2 Fixed Version Runtime"}:
                runtime[item["name"]] = item["version"]
        if archive.testzip() is not None:
            raise ValueError("ZIP has a corrupt entry.")
    return {"variant": variant, "sha256": expected, "files_verified": len(records),
            "webview2_bundled": webview, "runtimes": runtime, "notices": notices, "layout": layout}


def osv_targets(bom):
    ecosystems = {"pypi": "PyPI", "npm": "npm"}
    seen = set()
    for component in bom["components"]:
        if component["type"] == "file":
            continue
        purl = component.get("purl", "")
        kind, _, rest = purl.removeprefix("pkg:").partition("/")
        name, _, version = rest.rpartition("@")
        if kind in ecosystems and name and version and purl not in seen:
            seen.add(purl)
            yield {"package": {"ecosystem": ecosystems[kind], "name": unquote(name)}, "version": unquote(version)}


def scan_osv(bom, online):
    queries = sorted(osv_targets(bom), key=lambda item: (item["package"]["ecosystem"], item["package"]["name"], item["version"]))
    report = {"service": "OSV", "status": "not_scanned", "package_count": len(queries), "queried": 0, "findings": []}
    if not online or not queries:
        return report
    request = Request("https://api.osv.dev/v1/querybatch", data=json.dumps({"queries": queries}).encode("utf-8"),
                      headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urlopen(request, timeout=10) as response:
            results = json.load(response)["results"]
        if len(results) != len(queries):
            raise ValueError("Incomplete batch response.")
        report["findings"] = [{"query": query, "ids": [vuln["id"] for vuln in result.get("vulns", [])]}
                              for query, result in zip(queries, results) if result.get("vulns")]
        report["queried"] = len(queries)
        report["status"] = "partial" if any(result.get("next_page_token") for result in results) else "complete"
    except (URLError, OSError, ValueError, KeyError, TypeError) as error:
        report["status"] = "failed"
        report["error"] = str(error)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="action", required=True)
    graph = subcommands.add_parser("verify-graph")
    graph.add_argument("bom", type=Path)
    archive = subcommands.add_parser("verify-zip")
    archive.add_argument("zip", type=Path)
    archive.add_argument("variant", choices=("lite", "portable"))
    archive.add_argument("bom", type=Path)
    archive.add_argument("--require-build-info", action="store_true")
    scan = subcommands.add_parser("scan-osv")
    scan.add_argument("bom", type=Path)
    scan.add_argument("--online", action="store_true", help="Send public package names and versions to OSV.")
    args = parser.parse_args()
    if args.action == "verify-zip":
        result = verify_zip(args.zip, args.variant, args.bom, require_build_info=args.require_build_info)
    else:
        bom = json.loads(args.bom.read_text(encoding="utf-8"))
        result = verify_graph(bom) if args.action == "verify-graph" else scan_osv(bom, args.online)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.action == "scan-osv" and result["status"] != "complete":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
