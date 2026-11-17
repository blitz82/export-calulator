import json
from io import BytesIO
from zipfile import ZipFile

import pytest

from epms import supply_chain
from epms.oss_inventory import lock_dependency, sha256
from epms.packaging_checks import pe_is_x64


def test_lock_dependency_uses_the_nested_install_before_hoisted_copy():
    lock = {"node_modules/a": {}, "node_modules/c": {}, "node_modules/a/node_modules/c": {}}
    assert lock_dependency(lock, "node_modules/a", "c") == "npm:node_modules/a/node_modules/c"
    assert lock_dependency(lock, "node_modules/b", "c") == "npm:node_modules/c"


def test_graph_rejects_unresolved_references():
    bom = {"metadata": {"component": {"bom-ref": "epms"}},
           "components": [{"bom-ref": "first"}], "dependencies": [{"ref": "epms", "dependsOn": ["missing"]}]}
    with pytest.raises(ValueError, match="unknown component"):
        supply_chain.verify_graph(bom)


def test_scanner_scope_is_public_package_id_not_file_data():
    bom = {"components": [{"type": "library", "purl": "pkg:npm/%40tanstack/react-query@5.0.0"},
                          {"type": "library", "purl": "pkg:pypi/pillow@13.0.0"},
                          {"type": "file", "name": "private-name.js"}]}
    assert list(supply_chain.osv_targets(bom)) == [
        {"package": {"ecosystem": "npm", "name": "@tanstack/react-query"}, "version": "5.0.0"},
        {"package": {"ecosystem": "PyPI", "name": "pillow"}, "version": "13.0.0"},
    ]
    assert supply_chain.scan_osv(bom, online=False)["status"] == "not_scanned"


def test_zip_verifier_detects_wrong_embedded_inventory(tmp_path):
    target = tmp_path / "lite.zip"
    other = tmp_path / "reference.json"
    other.write_text('{"components":[]}', encoding="utf-8")
    with ZipFile(target, "w") as archive:
        archive.writestr("epms/sbom/epms-lite.cdx.json", json.dumps({"components": []}))
    target.with_suffix(".zip.sha256").write_text(sha256(target), encoding="ascii")
    with pytest.raises(ValueError, match="Embedded SBOM differs"):
        supply_chain.verify_zip(target, "lite", other)


def test_missing_notice_file_is_an_error_even_if_the_notice_list_exists():
    text = "Package | license identifier pending\n  licenses/example/LICENSE\n  License text pending\n"
    assert supply_chain.notice_status(text, {"licenses/example/LICENSE"}) == {
        "referenced_license_files": 1, "license_text_pending": 1, "license_identifier_pending": 1,
    }
    with pytest.raises(ValueError, match="original license"):
        supply_chain.notice_status(text, set())


def test_pe_architecture_requires_a_valid_x64_header():
    header = bytearray(80)
    header[:2] = b"MZ"
    header[0x3C:0x40] = (0x40).to_bytes(4, "little")
    header[0x40:0x46] = b"PE\0\0\x64\x86"
    assert pe_is_x64(BytesIO(header))
    header[0x44:0x46] = b"\x64\xaa"
    assert not pe_is_x64(BytesIO(header))
    assert not pe_is_x64(BytesIO(b"MZ"))


def test_strict_layout_does_not_accept_legacy_archives_without_build_information():
    with ZipFile(BytesIO(), "w") as archive:
        with pytest.raises(ValueError, match="build-info.json"):
            supply_chain.verify_layout(archive, [], "epms", "lite", require_build_info=True)
