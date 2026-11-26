"""Member-to-member agreements. Independent of export eligibility and calculations."""
import re
from contextlib import closing
from datetime import date
from decimal import Decimal, InvalidOperation
from hashlib import sha256

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Column, ForeignKey, Integer, JSON, LargeBinary, String, Table, UniqueConstraint, select

from epms import ocr, rules, store

documents = Table('member_contract_documents', store.metadata,
    Column('id', Integer, primary_key=True), Column('org', ForeignKey('organizations.id'), nullable=False),
    Column('sha', String, nullable=False), Column('name', String, nullable=False),
    Column('created', String, nullable=False), Column('pages', Integer, nullable=False),
    Column('data', LargeBinary, nullable=False), Column('extraction', JSON, nullable=False),
    UniqueConstraint('org', 'sha'))
contracts = Table('member_contracts', store.metadata,
    Column('id', Integer, primary_key=True), Column('org', ForeignKey('organizations.id'), nullable=False),
    Column('producer_id', ForeignKey('memberships.id', ondelete='SET NULL')),
    Column('exporter_id', ForeignKey('memberships.id', ondelete='SET NULL')),
    Column('document_id', ForeignKey('member_contract_documents.id'), nullable=False),
    Column('parent_id', ForeignKey('member_contracts.id')),
    Column('revision', Integer, nullable=False), Column('created', String, nullable=False),
    Column('updated', String, nullable=False), Column('data', JSON, nullable=False))
versions = Table('member_contract_versions', store.metadata,
    Column('id', Integer, primary_key=True), Column('contract_id', ForeignKey('member_contracts.id'), nullable=False),
    Column('revision', Integer, nullable=False), Column('time', String, nullable=False),
    Column('actor', String, nullable=False), Column('reason', String, nullable=False),
    Column('snapshot', JSON, nullable=False), UniqueConstraint('contract_id', 'revision'))
TABLES = (documents, contracts, versions)


class Agreement(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    title: str = Field(min_length=1, max_length=200)
    kind: str = Field(min_length=1, max_length=100)
    producer_id: int | None = Field(default=None, ge=1)
    exporter_id: int | None = Field(default=None, ge=1)
    document_id: int = Field(ge=1)
    parent_id: int | None = Field(default=None, ge=1)
    signed: str = Field(max_length=10)
    start: str = Field(max_length=10)
    end: str = Field(default='', max_length=10)
    auto_renew: bool = False
    terminated: str = Field(default='', max_length=10)
    product: str = Field(default='', max_length=200)
    contract_year: int | None = Field(default=None, ge=1900, le=9999)
    period_type: str = Field(default='기간 지정', pattern='^(연간 계약|기간 지정)$')
    quantity: str = Field(default='', max_length=40)
    unit: str = Field(default='', max_length=30)
    amount: str = Field(default='', max_length=40)
    currency: str = Field(default='', max_length=20)
    supply: str = Field(default='', max_length=4000)
    export: str = Field(default='', max_length=4000)
    settlement: str = Field(default='', max_length=4000)
    payment: str = Field(default='', max_length=4000)
    costs: str = Field(default='', max_length=4000)
    special: str = Field(default='', max_length=4000)
    memo: str = Field(default='', max_length=4000)
    evidence_pages: dict[str, int] = Field(default_factory=dict, max_length=20)
    review: str = Field(default='미검토', pattern='^(미검토|확인 필요|확인 완료)$')
    revision: int | None = Field(default=None, ge=1)
    reason: str = Field(min_length=1, max_length=500)


def reject(message, status=422):
    raise HTTPException(status, message)


def scoped(conn, table, org, key):
    row = conn.execute(table.select().where(table.c.org == org, table.c.id == key)).mappings().first()
    if row is None:
        reject('해당 품목의 계약 또는 PDF를 찾을 수 없습니다.', 404)
    return dict(row)


def checked_date(value, label, optional=False):
    if optional and not value:
        return ''
    try:
        parsed = date.fromisoformat(value)
        if parsed.isoformat() != value:
            raise ValueError()
        return value
    except ValueError:
        reject(f'{label}을 YYYY-MM-DD 형식으로 입력하세요.')


def identity(producer, exporter):
    a, b = (rules.business_number(x.get('number', '')) for x in (producer, exporter))
    if producer['id'] == exporter['id']:
        return True, []
    if not a or not b or not rules.valid_number(a) or not rules.valid_number(b):
        return False, ['사업자번호 누락 또는 형식 오류: 당사자 확인 필요']
    if a == b:
        return True, ['서로 다른 회원사 기록의 사업자번호가 같습니다. 중복 명부 확인 필요']
    return False, []


def assess(row, day):
    d = dict(row['data']); issues = []
    d.setdefault('contract_year', int(d['start'][:4]))
    d.setdefault('period_type', '기간 지정')
    row = dict(row, data=d)
    if d.get('terminated') and d['terminated'] <= day:
        state = '해지'
    elif day < d['start']:
        state = '예정'
    elif d['end'] and day > d['end']:
        if d['auto_renew']:
            state = '확인 필요'; issues.append('자동연장 조항의 실제 적용 여부를 확인하세요.')
        else:
            state = '만료'
    else:
        state = '유효'
    if not d['end'] and state == '유효':
        state = '확인 필요'; issues.append('종료일 미정: 현재 계약 유효 여부를 확인하세요.')
    same, identity_issues = identity(d['producer'], d['exporter'])
    issues += identity_issues
    if row['producer_id'] is None or row['exporter_id'] is None:
        issues.append('명부에서 삭제된 당사자: 체결 당시 정보로 표시합니다.')
    return dict(row, state=state, status_issues=issues, same_business=same)


def list_contracts(conn, org, day, member=None):
    rows = conn.execute(contracts.select().where(contracts.c.org == org).order_by(contracts.c.id.desc())).mappings()
    return [assess(dict(r), day) for r in rows if member is None or member in
            (r['data']['producer']['id'], r['data']['exporter']['id'])]


def save(conn, org, edit, actor, key=None):
    old = scoped(conn, contracts, org, key) if key else None
    if old and old['revision'] != edit.revision:
        reject('계약이 변경되었습니다. 다시 조회한 뒤 수정하세요.', 409)
    if key is None and edit.revision is not None:
        reject('신규 계약에는 수정 차수를 지정할 수 없습니다.')
    d = edit.model_dump(exclude={'revision', 'reason'})
    d['product'] = conn.execute(select(store.organizations.c.name).where(store.organizations.c.id == org)).scalar_one()
    document = scoped(conn, documents, org, edit.document_id)
    for field, label, optional in [('signed','체결일',False),('start','시작일',False),('end','종료일',True),('terminated','해지일',True)]:
        checked_date(d[field], label, optional)
    d['contract_year'] = edit.contract_year or int(d['start'][:4])
    if int(d['start'][:4]) != d['contract_year']:
        reject('계약 연도는 계약 시작일의 연도와 같아야 합니다.')
    if d['period_type'] == '연간 계약' and (d['start'] != f"{d['contract_year']}-01-01" or d['end'] != f"{d['contract_year']}-12-31"):
        reject('연간 계약은 해당 연도 1월 1일부터 12월 31일까지입니다. 연도 중 시작하는 계약은 기간 지정을 선택하세요.')
    if d['end'] and d['end'] < d['start']:
        reject('종료일은 시작일보다 빠를 수 없습니다.')
    if d['terminated'] and d['terminated'] < d['start']:
        reject('해지일은 시작일보다 빠를 수 없습니다.')
    for field, unit in [('quantity','unit'),('amount','currency')]:
        if d[field]:
            try:
                value = Decimal(d[field])
                if not value.is_finite() or value < 0:
                    raise InvalidOperation()
            except InvalidOperation:
                reject('물량과 금액은 0 이상의 유한한 숫자로 입력하세요.')
            if not d[unit]:
                reject('약정 물량의 단위 또는 금액의 통화를 입력하세요.')
    allowed_pages = {'signed','period','product','supply','export','settlement','payment','costs','special','parties'}
    if any(k not in allowed_pages or not 1 <= p <= document['pages'] for k,p in d['evidence_pages'].items()):
        reject('근거 항목과 PDF 페이지 범위를 확인하세요.')
    if d['review'] == '확인 완료' and not d['evidence_pages']:
        reject('확인 완료하려면 원문 근거 페이지를 한 개 이상 지정하세요.')
    for role in ('producer','exporter'):
        member_id = d[role+'_id']
        # Deleted memberships retain the immutable party snapshot when editing unrelated fields.
        if old and member_id is None and old[role+'_id'] is None:
            d[role] = old['data'][role]
        else:
            if member_id is None:
                reject('생산자회원사와 수출업체를 명부에서 선택하세요.')
            member = scoped(conn, store.memberships, org, member_id)
            d[role] = dict(id=member['id'], number=member['number'], name=member['data'].get('name',''),
                           kind=member['data'].get('kind',''))
            if old and member_id == old['data'][role]['id']:
                d[role] = old['data'][role]
    if old and (edit.document_id != old['document_id'] or edit.parent_id != old['parent_id'] or
                any(d[r]['id'] != old['data'][r]['id'] for r in ('producer','exporter'))):
        reject('당사자·원본 PDF·원계약 변경은 변경계약으로 등록하세요.')
    if edit.parent_id:
        parent = scoped(conn, contracts, org, edit.parent_id)
        if old is not None:
            if edit.parent_id == key:
                reject('자기 자신을 원계약으로 지정할 수 없습니다.')
        if any(d[r]['id'] != parent['data'][r]['id'] for r in ('producer','exporter')):
            reject('변경계약의 당사자는 원계약과 같아야 합니다.')
    now = store.now(); revision = old['revision'] + 1 if old else 1
    values = dict(org=org, producer_id=edit.producer_id, exporter_id=edit.exporter_id,
        document_id=edit.document_id, parent_id=edit.parent_id, revision=revision,
        created=old['created'] if old else now, updated=now, data=d)
    if old:
        conn.execute(contracts.update().where(contracts.c.id == key).values(**values))
    else:
        key = conn.execute(contracts.insert().values(**values)).inserted_primary_key[0]
    snapshot = dict(values, id=key)
    conn.execute(versions.insert().values(contract_id=key, revision=revision, time=now,
        actor=actor, reason=edit.reason, snapshot=snapshot))
    store.log(conn, org, actor, 'member_contract', key, edit.reason, old, snapshot)
    result = assess(snapshot, now[:10]); warnings = []
    for other in list_contracts(conn, org, now[:10]):
        if other['id'] == key:
            continue
        if other['document_id'] == edit.document_id:
            warnings.append(f"계약 #{other['id']}과 같은 PDF입니다.")
        od = other['data']
        if all(d[r]['id'] == od[r]['id'] for r in ('producer','exporter')) and d['start'] <= (od['end'] or '9999-12-31') and od['start'] <= (d['end'] or '9999-12-31'):
            warnings.append(f"계약 #{other['id']}과 당사자 및 기간이 겹칩니다. 복수·변경계약인지 확인하세요.")
    return dict(result, warnings=warnings)


def extract_document(data):
    pages = []; candidates = []
    # Reuse the local PDFium parser. Declaration-specific OCR templates are intentionally not applied.
    with ocr.PDF_LOCK, ocr.open_pdf(data) as doc:
        if not 1 <= len(doc) <= ocr.MAX_PAGES:
            reject(f'계약 PDF는 1~{ocr.MAX_PAGES}쪽이어야 합니다.')
        for i in range(len(doc)):
            warning = ''
            try:
                with closing(doc[i]) as page, closing(page.get_textpage()) as textpage:
                    text = textpage.get_text_range()[:40000]
            except Exception:
                text = ''; warning = '텍스트 추출 실패: 원본을 보고 수동 입력하세요.'
            if not text.strip():
                warning = warning or '스캔 또는 텍스트 없는 페이지: 원본을 보고 수동 입력하세요.'
            pages.append(dict(page=i+1, text=text, warning=warning))
            patterns = {'producer':'생산자(?:회원사)?', 'exporter':'수출업체|수출자', 'signed':'계약체결일|체결일',
                'start':'계약시작일|시작일', 'end':'계약종료일|종료일', 'product':'대상품목|품목',
                'supply':'공급약정', 'export':'수출약정', 'settlement':'공동정산', 'payment':'대금지급', 'costs':'비용부담'}
            for field, label in patterns.items():
                for match in re.finditer(r'(?:'+label+r')[ \t]*[:：][ \t]*([^\r\n]{1,300})', text):
                    candidates.append(dict(field=field, value=match[1].strip(), page=i+1, confirmed=False))
    return dict(pages=pages, candidates=candidates, method='로컬 PDF 텍스트 · 라벨 후보', external_transmission=False)


def install(app, db, organization, actor):
    from fastapi import Request
    from fastapi.responses import Response
    from starlette.concurrency import run_in_threadpool
    from urllib.parse import quote

    path = '/api/org/{org}/contracts'

    @app.get(path)
    def listing(org: int, day: str = '', member: int | None = None):
        day = checked_date(day or store.now()[:10], '기준일')
        with db.connect() as conn:
            organization(conn, org)
            return list_contracts(conn, org, day, member)

    @app.post(path)
    def create(org: int, edit: Agreement, request: Request):
        with db.begin() as conn:
            conn.exec_driver_sql('BEGIN IMMEDIATE'); organization(conn, org)
            return save(conn, org, edit, actor(request))

    @app.put(path+'/{key}')
    def update(org: int, key: int, edit: Agreement, request: Request):
        with db.begin() as conn:
            conn.exec_driver_sql('BEGIN IMMEDIATE'); organization(conn, org)
            return save(conn, org, edit, actor(request), key)

    @app.get(path+'/{key}/history')
    def history(org: int, key: int):
        with db.connect() as conn:
            scoped(conn, contracts, org, key)
            return [dict(r) for r in conn.execute(versions.select().where(versions.c.contract_id == key).order_by(versions.c.revision.desc())).mappings()]

    def metadata(row):
        return {k:v for k,v in row.items() if k != 'data'}

    @app.post(path+'/documents/upload')
    async def upload(org: int, name: str, request: Request):
        if not name.lower().endswith('.pdf') or len(name)>240 or any(c in name for c in '/\\:\r\n'):
            reject('PDF 파일명을 확인하세요.')
        with db.connect() as conn:
            organization(conn, org)
        chunks = []; size = 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > ocr.MAX_BYTES:
                reject('PDF는 20MB 이하여야 합니다.', 413)
            chunks.append(chunk)
        data = b''.join(chunks); digest = sha256(data).hexdigest()
        with db.connect() as conn:
            existing = conn.execute(documents.select().where(documents.c.org == org, documents.c.sha == digest)).mappings().first()
            if existing:
                return dict(metadata(existing), duplicate=True)
        try:
            extraction = await run_in_threadpool(extract_document, data)
        except ValueError as e:
            reject(str(e))
        with db.begin() as conn:
            conn.exec_driver_sql('BEGIN IMMEDIATE')
            existing = conn.execute(documents.select().where(documents.c.org == org, documents.c.sha == digest)).mappings().first()
            if existing:
                return dict(metadata(existing), duplicate=True)
            values = dict(org=org, sha=digest, name=name, created=store.now(), pages=len(extraction['pages']), data=data, extraction=extraction)
            key = conn.execute(documents.insert().values(**values)).inserted_primary_key[0]
            store.log(conn, org, actor(request), 'member_contract_pdf', key, '계약 PDF 등록', None, dict(name=name, sha=digest))
            return dict(metadata(dict(values,id=key)), duplicate=False)

    @app.get(path+'/documents/{key}')
    def document_info(org: int, key: int):
        with db.connect() as conn:
            return metadata(scoped(conn, documents, org, key))

    @app.get(path+'/documents/{key}/original')
    def original(org: int, key: int):
        with db.connect() as conn:
            row = scoped(conn, documents, org, key)
        return Response(row['data'], media_type='application/pdf', headers={'Content-Disposition':"attachment; filename*=UTF-8''"+quote(row['name'])})

    @app.get(path+'/documents/{key}/page/{page}')
    def preview(org: int, key: int, page: int):
        with db.connect() as conn:
            row = scoped(conn, documents, org, key)
        try:
            return Response(ocr.page_image(row['data'], page), media_type='image/png')
        except ValueError as e:
            reject(str(e))
