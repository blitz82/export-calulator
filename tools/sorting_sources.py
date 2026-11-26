"""Read authorized local sources; private extraction never belongs in a release."""
import hashlib
import json
from pathlib import Path
from zipfile import ZipFile
from xml.etree import ElementTree as ET

from openpyxl import load_workbook
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'output' / 'sorting-private'


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    inventory = []
    for index, path in enumerate(sorted((ROOT / 'sample2').rglob('*'))):
        if not path.is_file():
            continue
        entry = dict(source=str(path.relative_to(ROOT / 'sample2')),
                     sha256=hashlib.sha256(path.read_bytes()).hexdigest(), size=path.stat().st_size)
        text = ''
        try:
            if path.suffix == '.docx':
                with ZipFile(path) as archive:
                    root = ET.fromstring(archive.read('word/document.xml'))
                ns = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
                text = '\n'.join(''.join(p.itertext()) for p in [])
                text = '\n'.join(''.join(t.text or '' for t in p.findall('.//w:t', ns))
                                 for p in root.findall('.//w:p', ns))
            elif path.suffix == '.pdf':
                reader = PdfReader(path)
                entry['pages'] = len(reader.pages)
                pages = [page.extract_text() for page in reader.pages]
                entry['text_pages'] = sum(bool(p.strip()) for p in pages)
                text = '\n'.join(f'\nPAGE {i+1}\n{page}' for i, page in enumerate(pages))
            elif path.suffix == '.xlsx':
                book = load_workbook(path, read_only=True, data_only=False, keep_links=False)
                entry['sheets'] = []
                parts = []
                for sheet in book:
                    entry['sheets'].append(dict(name=sheet.title, rows=sheet.max_row, cols=sheet.max_column))
                    parts.append(f'SHEET {sheet.title}')
                    for row in sheet.iter_rows():
                        parts.append(' | '.join(f'{cell.coordinate}:{cell.value}' for cell in row if cell.value is not None))
                book.close()
                text = '\n'.join(parts)
            elif path.suffix == '.hwp':
                entry['status'] = 'BINARY_HWP_NOT_PARSED'
            if text:
                target = OUT / f'{index:03d}.txt'
                target.write_text(text, encoding='utf-8')
                entry['extraction'] = target.name
        except Exception as exc:
            entry['status'] = type(exc).__name__
        inventory.append(entry)
    (OUT / 'inventory.json').write_text(json.dumps(inventory, ensure_ascii=False, indent=2), encoding='utf-8')
    print('Private source inventory:', len(inventory), 'files')
    for item in inventory:
        if item['source'].endswith('.docx') or item['source'].endswith('.xlsx') or '\\' not in item['source']:
            print(item['source'], item.get('extraction'), item.get('pages', ''), item.get('sheets', ''))


if __name__ == '__main__':
    main()
