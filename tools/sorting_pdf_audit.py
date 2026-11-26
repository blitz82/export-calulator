"""Local, resumable OCR of authorized samples. Sensitive results stay private."""
import argparse,json,hashlib,sys,time
from pathlib import Path
root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root))
from epms import sorting_pdf_review as review,ocr,sorting as s

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--limit-pages',type=int,default=1000)
    parser.add_argument('--only',default='')
    args=parser.parse_args()
    out=root/'output/sorting-private/pdf-page-audit';out.mkdir(parents=True,exist_ok=True)
    items=json.loads((root/'output/sorting-private/inventory.json').read_text(encoding='utf-8'))
    unique={r['sha256']:r for r in items if r['source'].endswith('.pdf') and args.only in r['source']}
    done=0;failures=0;start=time.monotonic()
    for sha,item in unique.items():
        file=root/'sample2'/item['source']
        if not file.resolve().is_relative_to((root/'sample2').resolve()) or hashlib.sha256(file.read_bytes()).hexdigest()!=sha:
            raise ValueError('Source changed or is outside authorized sample2')
        for page in range(1,item['pages']+1):
            target=out/f'{sha}-{page:03d}.json'
            if target.exists():
                previous=json.loads(target.read_text(encoding='utf-8'))
                if (previous.get('version')==review.VERSION and previous.get('extractor')==ocr.EXTRACTION_VERSION
                    and previous.get('sha')==sha and previous.get('page')==page
                    and s.digest(previous.get('result'))==previous.get('result_hash')):
                    continue
            if done>=args.limit_pages:
                return
            began=time.monotonic()
            result=review.extract_page(file.read_bytes(),page)
            payload=dict(sha=sha,page=page,extractor=ocr.EXTRACTION_VERSION,version=review.VERSION,
                         result=result,result_hash=s.digest(result),seconds=round(time.monotonic()-began,3),human_reviewed=False)
            target.write_text(json.dumps(payload,ensure_ascii=False),encoding='utf-8')
            done+=1
            failures=failures+1 if 'Windows OCR' in result.get('warning','') else 0
            print(json.dumps(dict(processed_now=done,page=page,rows=len(result['rows']),seconds=payload['seconds'],warning=bool(result.get('warning')),elapsed=round(time.monotonic()-start,1))),flush=True)
            if failures>=3:
                print('BLOCKED: Windows OCR unavailable on three consecutive pages',flush=True)
                raise SystemExit(2)

if __name__=='__main__':
    main()
