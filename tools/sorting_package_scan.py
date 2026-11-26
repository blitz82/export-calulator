"""Read-only check of local validation archives and protected source files."""
import hashlib
import json
from pathlib import Path
from zipfile import ZipFile

root=Path(__file__).resolve().parents[1]
report={'archives':[],'protected_sources':{}}
baseline=json.loads((root/'docs/sorting/BASELINE_SOURCE_HASHES.json').read_text(encoding='utf-8'))
for relative in ('epms/rules.py','epms/store.py','epms/books.py','epms/submission.py','epms/ocr.py','epms/ocr_profiles.py','epms/ocr_audit.py','web/api.ts'):
    original=baseline.get(relative.replace('/', '\\')) or baseline.get(relative)
    actual=hashlib.sha256((root/relative).read_bytes()).hexdigest()
    report['protected_sources'][relative]='PASS' if original==actual else 'CHANGED'
for variant in ('lite','portable'):
    folder=root/'build'/f'sorting-validation-{variant}'
    files=list(folder.glob('*.zip'))
    if not files:
        continue
    for path in files:
        with ZipFile(path) as archive:
            bad=[]
            for name in archive.namelist():
                parts=name.lower().split('/')
                # Vendor license paths such as licenses/.../data contain required notices,
                # whereas the application's top-level data directory holds user records.
                if (any(p in parts for p in ('sample2','sorting-private'))
                        or (len(parts)>1 and parts[1]=='data')
                        or name.lower().endswith(('.db','.db-wal','.db-shm','.docx','.hwp','.env'))):
                    bad.append(name)
            launcher=any(name.endswith('Start-EPMS-Sorting-Review.cmd') for name in archive.namelist())
            guide=any('sorting-guide/USER_MANUAL.md' in name for name in archive.namelist())
            source_bytes={hashlib.sha256(p.read_bytes()).hexdigest() for p in (root/'sample2').rglob('*') if p.is_file()}
            original_matches=[]
            for item in archive.infolist():
                if item.is_dir() or item.file_size>20_000_000:
                    continue
                if hashlib.sha256(archive.read(item)).hexdigest() in source_bytes:
                    original_matches.append(item.filename)
        sha=hashlib.sha256(path.read_bytes()).hexdigest()
        report['archives'].append(dict(variant=variant,path=str(path.relative_to(root)),size=path.stat().st_size,
            sha256=sha,sensitive_paths=bad,source_hash_matches=original_matches,review_launcher=launcher,guide=guide,
            status='PASS' if not bad and not original_matches and launcher and guide else 'FAIL'))
out=root/'output/sorting-tests/package-source-scan.json'
out.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(report,ensure_ascii=False,indent=2))
