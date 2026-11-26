import json, zipfile, hashlib, sqlite3
from pathlib import Path
from PIL import Image, ImageOps, ImageDraw, ImageFont
import pypdfium2 as pdfium

p=Path('output/menu-demo-20261125')
manifest=json.loads((p/'captures.json').read_text(encoding='utf8'))
assert len(manifest['captures'])==20 and not manifest['errors']
summary=json.loads((p/'source-summary.json').read_text(encoding='utf8'))
assert hashlib.sha256(Path('C:/Users/user/Downloads/epms.db').read_bytes()).hexdigest()==summary['sha256']
for item in manifest['captures']:
    with Image.open(p/item['file']) as im:im.verify()
selected=['01-home.png','02-members.png','04-contracts.png','07-facilities.png','08-declarations.png','09-submission.png','16-settlement-result.png','17-audit.png','18-checks.png','19-settings.png','20-help.png']
font=ImageFont.truetype('C:/Windows/Fonts/malgun.ttf',30)
canvas=Image.new('RGB',(2880,4*730+125),'#eaf1ec');draw=ImageDraw.Draw(canvas)
draw.text((30,24),'공동정산 수출실적 · 메뉴별 실제 화면 (가상 데이터)',font=font,fill='#174f36')
draw.text((30,70),'각 화면의 고해상도 원본은 화면모음.html에서 확인할 수 있습니다.',font=ImageFont.truetype('C:/Windows/Fonts/malgun.ttf',23),fill='#365444')
byfile={x['file']:x for x in manifest['captures']}
for i,file in enumerate(selected):
    x=(i%3)*960+12;y=(i//3)*730+125
    draw.rounded_rectangle((x,y,x+936,y+710),radius=8,fill='white')
    draw.text((x+14,y+9),f'{i+1:02d}. '+byfile[file]['title'],font=font,fill='#174f36')
    im=Image.open(p/file).convert('RGB');im=ImageOps.contain(im,(910,650));canvas.paste(im,(x+13+(910-im.width)//2,y+52))
canvas.save(p/'00-menu-overview.jpg',quality=94)
pdf=pdfium.PdfDocument(p/'가상_공동선별비_참고계산.pdf')
for i in range(len(pdf)):pdf[i].render(scale=2).to_pil().save(p/f'21-result-pdf-page-{i+1}.png')
pdf.close()
html=p/'화면모음.html';text=html.read_text(encoding='utf8')
text+='<article><h2>실제 생성된 정산 결과 PDF</h2><p><a href="가상_공동선별비_참고계산.pdf">결과 PDF 열기 (2쪽)</a></p><img src="21-result-pdf-page-1.png"><img src="21-result-pdf-page-2.png"></article>'
html.write_text(text,encoding='utf8')
with sqlite3.connect(p/'demo.db') as c:
    assert c.execute('pragma integrity_check').fetchone()[0]=='ok'
    assert c.execute('pragma foreign_key_check').fetchall()==[]
    c.execute('pragma wal_checkpoint(TRUNCATE)')
verification=dict(original_sha256_unchanged=True,captured_screens=20,browser_errors=manifest['errors'],database_integrity='ok',foreign_key_violations=0,reference_amount='249000',reference_kg='2490',source_leftover_kg=['0','200','200'],demonstration_declaration_leftover_kg=['0','0','200'],source='current web build / isolated copy of supplied database')
(p/'verification.json').write_text(json.dumps(verification,ensure_ascii=False,indent=2),encoding='utf8')
archive=p/'메뉴별_가상업무_화면캡처.zip'
files=[p/x['file'] for x in manifest['captures']]+[p/'00-menu-overview.jpg',html,p/'가상_공동선별비_참고계산.pdf',p/'21-result-pdf-page-1.png',p/'21-result-pdf-page-2.png',p/'verification.json']
with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
    for file in files:z.write(file,file.name)
print(json.dumps(dict(verification=verification,archive_bytes=archive.stat().st_size),ensure_ascii=False))
