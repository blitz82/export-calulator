"""Collect notices from the installed build inputs and hash packaged binaries."""
import argparse
import ast
import hashlib
from html import escape
import importlib.metadata as metadata
import json
import re
import shutil
import sys
from pathlib import Path, PurePosixPath
from urllib.parse import quote

from packaging.requirements import Requirement


ROOT = Path(__file__).resolve().parents[1]
NOTICE = re.compile(r"^(?:licen[sc]es?|copying|notice|copyright|legal)(?:[-_.].*)?$", re.I)


def canonical_name(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def copy_notice(origin, relative, target):
    path = target / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(origin, path)
    return relative.as_posix()


def frozen_distributions(analysis):
    if analysis is None:
        return []

    def inputs(node):
        if isinstance(node, (tuple, list)):
            if len(node) == 3 and isinstance(node[1], str) and isinstance(node[2], str) and node[2] in {
                "PYMODULE", "DATA", "EXTENSION", "BINARY"
            }:
                yield Path(node[1]).resolve()
            else:
                for item in node:
                    yield from inputs(item)

    paths = set(inputs(ast.literal_eval(analysis.read_text(encoding="utf-8"))))
    return [dist.name for dist in metadata.distributions()
            if any(dist.locate_file(file).resolve() in paths for file in dist.files or ())]


def python_components(target, analysis):
    pending = [Requirement(line) for line in (ROOT / "requirements.txt").read_text().splitlines() if line.strip()]
    direct = {canonical_name(item.name): item for item in pending}
    detected = {canonical_name(name) for name in frozen_distributions(analysis)}
    pending.extend(Requirement(name) for name in ["pyinstaller", *sorted(detected)])
    seen = set()
    while pending:
        requirement = pending.pop()
        if requirement.marker and not requirement.marker.evaluate():
            continue
        dist = metadata.distribution(requirement.name)
        name = canonical_name(dist.name)
        if name in seen:
            continue
        seen.add(name)
        requirements = [Requirement(value) for value in dist.requires or ()]
        requirements = [item for item in requirements if item.marker is None or item.marker.evaluate()]
        pending.extend(requirements)
        if name in direct and dist.version not in direct[name].specifier:
            raise ValueError(f"Installed {name} {dist.version} does not match {direct[name]}")
        notices = []
        expression = dist.metadata.get("License-Expression", "")
        for file in dist.files or ():
            if file.parts[0].endswith(".dist-info") and (
                    NOTICE.fullmatch(file.name) or "licenses" in file.parts[1:-1]):
                origin = dist.locate_file(file)
                if origin.is_file():
                    notices.append(copy_notice(origin, Path("python", name, *file.parts[1:]), target))
        if name == "proxy-tools" and dist.version == "0.1.0" and not notices:
            original = ROOT / "licenses/upstream/python/proxy-tools/0.1.0/LICENSE.txt"
            if sha256(original) != "a428fb8a2e762af3eb0a6edbbb88e9b42ccfee80fd9b423958bcacf9b9abbfe4":
                raise ValueError("Verified proxy_tools license text has been modified.")
            notices.append(copy_notice(original, Path("python", name, dist.version, "LICENSE.txt"), target))
            expression = "BSD-2-Clause"
        yield dict(name=dist.name, version=dist.version, purl=f"pkg:pypi/{name}@{dist.version}",
                   ref=f"pypi:{name}", edges=[f"pypi:{canonical_name(item.name)}" for item in requirements],
                   direct=name in direct, observed=name in detected,
                   license=expression, notices=sorted(notices))
    python_notice = Path(sys.base_prefix) / "LICENSE.txt"
    notices = [copy_notice(python_notice, Path("runtime", "Python-LICENSE.txt"), target)] if python_notice.is_file() else []
    yield dict(name="CPython", version=sys.version.split()[0], license="PSF-2.0", notices=notices,
               ref="runtime:cpython", direct=True, supplier="Python Software Foundation")


def bundle_packages(dist):
    manifest = dist / "bundle-components.json"
    if not manifest.is_file():
        return set()
    record = json.loads(manifest.read_text(encoding="utf-8"))
    if record["lock_sha256"] != sha256(ROOT / "package-lock.json"):
        raise ValueError("Vite bundle inventory was generated using a different lockfile.")
    return verify_bundle_chunks(dist, record)


def verify_bundle_chunks(dist, record):
    packages = set()
    files = [chunk["name"] for chunk in record["chunks"]]
    if len(set(files)) != len(files) or set(files) != {file.relative_to(dist).as_posix() for file in dist.rglob("*.js")}:
        raise ValueError("Vite bundle JavaScript paths do not match inventory.")
    for chunk in record["chunks"]:
        relative = PurePosixPath(chunk["name"])
        if relative.is_absolute() or ".." in relative.parts or sha256(dist / relative) != chunk["sha256"]:
            raise ValueError("Vite bundle chunk hash does not match inventory.")
        packages.update(chunk["packages"])
    return packages


def lock_dependency(packages, folder, dependency):
    paths = (PurePosixPath(folder), *PurePosixPath(folder).parents)
    for parent in paths:
        key = (parent / "node_modules" / dependency).as_posix()
        if key in packages:
            return f"npm:{key}"
    return None


def js_components(target, observed):
    packages = json.loads((ROOT / "package-lock.json").read_text(encoding="utf-8"))["packages"]
    if observed - packages.keys():
        raise ValueError("Vite bundle inventory contains a package absent from the lockfile.")
    direct = {f"node_modules/{key}" for key in packages[""].get("dependencies", {})}
    for folder, package in packages.items():
        if not folder or (package.get("dev") and folder not in observed):
            continue
        path = ROOT / folder
        name = folder.rsplit("node_modules/", 1)[-1]
        version = package["version"]
        manifest = path / "package.json"
        if folder in observed and not manifest.is_file():
            raise ValueError(f"Observed npm package is missing: {name}")
        if manifest.is_file() and json.loads(manifest.read_text(encoding="utf-8")).get("version") != version:
            raise ValueError(f"Installed npm version differs from lockfile: {name}")
        notices = []
        for file in path.glob("*"):
            if file.is_file() and NOTICE.fullmatch(file.name):
                notices.append(copy_notice(file, Path("javascript", name, version, file.name), target))
        yield dict(name=name, version=version, purl=f"pkg:npm/{quote(name, safe='/')}@{version}",
                   ref=f"npm:{folder}", direct=folder in direct,
                   observed=folder in observed,
                   edges=[lock_dependency(packages, folder, dependency) for dependency in (
                       package.get("dependencies", {}) | package.get("optionalDependencies", {})
                       | package.get("peerDependencies", {}))],
                   license=package.get("license", ""), notices=sorted(notices))


def sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def collect(destination, variant, analysis=None):
    licenses = destination / "licenses"
    dist = ROOT / "dist" if variant == "development" else destination / "_internal" / "dist"
    observed = bundle_packages(dist)
    components = sorted([*python_components(licenses, analysis), *js_components(licenses, observed)], key=lambda item: item["name"])
    pdfium = next((item for item in components if item["ref"] == "pypi:pypdfium2"), None)
    runtime_json = (Path(metadata.distribution("pypdfium2").locate_file("pypdfium2_raw/version.json"))
                    if variant == "development" else destination / "_internal/pypdfium2_raw/version.json")
    if pdfium and runtime_json.is_file():
        version = json.loads(runtime_json.read_text(encoding="utf-8"))
        pdfium["edges"].append("runtime:pdfium")
        components.append(dict(name="PDFium", version=".".join(str(version[key]) for key in ("major", "minor", "build", "patch")),
                               license="", notices=[name for name in pdfium["notices"] if "BUILD_LICENSES" in name],
                               ref="runtime:pdfium", observed=True))
    binaries = []
    if variant == "portable":
        credits = destination / "WebView2" / "show_third_party_software_licenses.bat"
        terms = ROOT / "licenses/upstream/runtime/WebView2-Fixed-Version-LICENSE.html"
        notices = [copy_notice(terms, Path("runtime", terms.name), licenses)]
        if credits.is_file():
            notices.append(copy_notice(credits, Path("runtime", "WebView2-show-credits.bat"), licenses))
        executable = destination / "WebView2" / "msedgewebview2.exe"
        manifests = sorted(executable.parent.glob("*.manifest"))
        version = manifests[0].stem if len(manifests) == 1 else ""
        components.append(dict(name="Microsoft Edge WebView2 Fixed Version Runtime", version=version,
                               license="LicenseRef-Microsoft-WebView2-Fixed", notices=notices,
                               ref="runtime:webview2", direct=True, observed=True, supplier="Microsoft"))
    if variant != "development":
        for file in destination.rglob("*"):
            if not file.is_file():
                continue
            if file.suffix.lower() in {".pdf", ".db", ".sqlite", ".sqlite3", ".db-wal", ".db-shm"}:
                raise ValueError(f"Unexpected data file in package: {file.relative_to(destination)}")
            if file.suffix.lower() in {".dll", ".exe", ".pyd", ".js"}:
                name = file.relative_to(destination).as_posix()
                binaries.append({"type": "file", "name": name, "bom-ref": f"file:{name}",
                                 "hashes": [{"alg": "SHA-256", "content": sha256(file)}]})
    notices = ["EPMS Third-Party Notices", "", "Inventory from local build inputs. Review pending.",
               "Windows OCR and Windows PowerShell are provided by the operating system.", ""]
    for item in components:
        notices.append(f"{item['name']} {item['version']} | {item['license'] or 'license identifier pending'}")
        notices.extend(f"  licenses/{name}" for name in item["notices"])
        if not item["notices"]:
            notices.append("  License text pending")
    notice_text = "\n".join(notices) + "\n"
    (destination / "THIRD_PARTY_NOTICES.txt").write_text(notice_text, encoding="utf-8")
    (destination / "THIRD_PARTY_NOTICES.md").write_text("\n".join(
        [f"# {notices[0]}", "", *notices[2:5], *(f"    {line}" for line in notices[5:])]) + "\n", encoding="utf-8")
    listing = []
    for item in components:
        links = " ".join(f'<a href="{quote(name)}">{escape(Path(name).name)}</a>' for name in item["notices"])
        listing.append(f"<li>{escape(item['name'])} {escape(item['version'])} · "
                       f"{escape(item['license'] or '라이선스 식별자 확인 중')}<br>{links or '로컬 원문 확인 중'}</li>")
    (licenses / "index.html").write_text(
        '<!doctype html><html lang="ko"><meta charset="utf-8"><title>오픈소스 소프트웨어 정보</title>'
        '<h1>오픈소스 소프트웨어 정보</h1><p>이 빌드의 구성요소와 로컬 라이선스 원문입니다. '
        '검토가 진행 중입니다.</p><ul>' + "\n".join(listing) + "</ul></html>", encoding="utf-8")
    sbom_dir = destination / "sbom"
    sbom_dir.mkdir(exist_ok=True)
    entries = []
    for item in components:
        entry = {"type": "library", "name": item["name"], "version": item["version"], "bom-ref": item["ref"],
                 "properties": [{"name": "epms:observed-in-build", "value": str(item.get("observed", False)).lower()}]}
        if item.get("purl"):
            entry["purl"] = item["purl"]
        if item.get("supplier"):
            entry["supplier"] = {"name": item["supplier"]}
        if item["license"]:
            entry["licenses"] = [{"license": {"name": item["license"]}}]
        entries.append(entry)
    refs = {item["ref"] for item in components}
    dependencies = [{"ref": "epms", "dependsOn": sorted(item["ref"] for item in components if item.get("direct"))}]
    dependencies.extend({"ref": item["ref"], "dependsOn": sorted(set(item.get("edges", [])) & refs)} for item in components)
    inputs = [ROOT / "requirements.txt", ROOT / "requirements-dev.txt", ROOT / "package-lock.json",
              ROOT / "epms" / "ocr_unipass.json"]
    bom = {"bomFormat": "CycloneDX", "specVersion": "1.6", "version": 1,
           "metadata": {"component": {"type": "application", "name": f"EPMS {variant}", "bom-ref": "epms"},
                        "properties": [{"name": "epms:inventory-scope", "value": "build inputs and packaged file hashes"}] +
                        [{"name": f"epms:sha256:{path.name}", "value": sha256(path)} for path in inputs]},
           "components": entries + sorted(binaries, key=lambda item: item["name"]), "dependencies": dependencies}
    (sbom_dir / f"epms-{variant}.cdx.json").write_text(
        json.dumps(bom, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{variant}: {len(components)} build inputs, {len(binaries)} packaged file hashes, "
          f"{sum(not item['notices'] for item in components)} inputs without local notices, "
          f"{len(observed)} npm packages observed in Vite chunks.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("destination", type=Path)
    parser.add_argument("variant", choices=["development", "lite", "portable"])
    parser.add_argument("--analysis", type=Path)
    options = parser.parse_args()
    collect(options.destination, options.variant, options.analysis)
