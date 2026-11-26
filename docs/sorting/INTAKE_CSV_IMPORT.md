> 종전 설계·검토 기록 보관 문서입니다. 아래 메뉴·작성·결재·지급 절차는 현재 제품에서 종료되었습니다. 현재 사용법은 [공동선별비 정산 안내](../SETTLEMENT_WORKFLOW.md)를 따르세요.

# 생산자 입고 CSV 매핑·등록 (2026-11-23)

마스터 M3의 ERP/Excel 원본 추적 중 범용 CSV 입고 어댑터를 구현했다. 특정 ERP의 실제 서식·API는 제공되지 않았으므로 전용 ERP 연동 또는 실제 원물 전수 검증으로 표시하지 않는다. 기존 Excel 선별 작업 가져오기와 생산 원장을 유지하며 신규 테이블/migration/의존성은 없다. .git 부재로 SHA/브랜치/HEAD 확인은 BLOCKED이며 push·공개 release·운영 DB 적용은 하지 않는다.

## 사용 순서

공동선별비 검토 → 입고 CSV에서 UTF-8(BOM 가능) 또는 CP949를 고른 뒤 CSV를 올린다. 시설과 원본 시스템명을 입력하고 머리글의 열을 매핑한다. 원본 입고번호·생산자 가명 ID·품목·입고일·입고량·단위가 필수이며 품종·색상·규격·메모는 선택이다. 각 필드는 서로 다른 열이어야 한다. 시스템명 앞뒤 공백을 제거하며 등록번호는 시스템명:원본번호이다. 원본번호의 앞자리 0을 숫자로 변환하지 않는다. 시스템명은 기관이 일관된 코드로 관리해야 하며 자동 ERP 식별을 하지 않는다.

원본 머리글의 위치를 직접 선택한다. 미리보기는 입고·예약·신청을 생성하지 않는다. 오류 없는 행만 선택해 한 번에 최대 1,000행 등록한다. 파일은 20MB, 데이터 5,000행, 128열까지이다. 중복/빈 머리글, 파일 내 중복 입고번호, 시설 품목 불일치, kg 이외 단위, 빈 필수값, 필수값 수식형 문자열, 날짜/물량 형식과 정밀도 오류를 확인한다. 필수값은 공백을 정리해 검증하되 원문과 원본 셀은 별도 보존한다. 단위 환산·수식 평가·임의 날짜 변환은 하지 않는다.

검색/오류 필터와 50행 페이지 이동을 제공한다. 오류 없는 처음 1,000행 선택은 현재 검색 결과가 아니라 파일 전체의 유효 행 기준이다. CSV 행 번호는 머리글을 1로 세는 논리 레코드 번호이다. 따옴표 안 줄바꿈이 있는 경우 텍스트 파일의 물리적 줄 번호와 다를 수 있다. 빈 논리 행은 등록하지 않는다.

## 무결성·재전송·변경 자료

서버가 원본 SHA256을 확인하고 매핑 버전 intake-csv-1, 인코딩·시설·시스템·열 위치의 config/hash, 원본 셀·매핑 원문·논리 행을 입고 data.source에 저장한다. 미리보기 hash가 현재 원본/매핑과 다르면 등록을 거절한다. 스냅샷 hash는 진본보증이나 전자서명이 아니다.

선택 행은 BEGIN IMMEDIATE 한 transaction에서 등록한다. 중간 한 행이라도 원본번호 충돌/검증 오류가 발생하면 새 입고와 성공 이력을 모두 rollback한다. 같은 요청키/같은 내용의 재전송은 기존 결과를 반환한다. 같은 요청키의 다른 내용은 409이다. 같은 원본·같은 매핑의 다른 요청키는 이미 등록한 입고를 재사용하고 성공한 재제출 이력만 추가할 수 있다. 화면은 신규/재사용 건수를 구분한다. 동시 같은 요청도 재고를 한 번만 생성한다.

다른 파일의 같은 시스템:원본번호는 내용이 같아 보여도 증빙/원본 추적이 다르므로 자동 덮어쓰지 않는다. 별도의 정정 검토가 필요하며 번호를 임의 변경해 중복 입고를 만들지 않는다. 기존 원장 정정·이월 재사용·이미 배분한 재고 자동 조정은 이번 기능에 포함하지 않는다. 가져오기 성공 이력은 원본 증빙·시설·시스템·매핑 버전/hash·선택 행·생성/재사용 입고 ID·담당자·시각을 보존한다. 화면은 최근 성공 이력 100건과 원본 내려받기를 제공한다. 실패한 transaction의 별도 성공 이력은 남지 않는다. 업로드된 원본은 증빙으로 보존된다.

## API·시험 추적

기존 조직/세션과 기능 OFF를 따르는 intake-import-columns(GET), intake-import-preview(POST), intake-import-commit(POST), intake-import-history(GET)를 추가했다. 구현은 epms/sorting_intake_import.py, sorting_api.py와 web/sorting-intake-import.tsx/sorting.tsx이다. sorting_production.intake의 기존 원본번호 검사는 해당 ID의 JSON 조회로 좁혀 가져오기 때 전체 입고 본문을 매 행 역직렬화하지 않는다. 기존 직접 입고/선별/예약 계약은 유지한다.

BR-201/204와 M3의 원본 ID·행별 오류·매핑 버전·재제출 추적을 보완한다. tests/test_sorting_intake_import.py는 원문/앞자리 0/규격 등 보존, 읽기 전용 미리보기, 재전송·새 요청 재사용, 변경 파일 충돌과 전체 rollback, 중복/수식/단위/날짜 오류, stale 매핑, 조직·해시 변조, CP949·중복 헤더·5,001행 제한, 동시 등록을 검증한다. tests/browser/sorting.spec.ts는 CSV 파일을 실제 업로드해 열을 선택하고 등록·재전송한 뒤 잔량 원장에서 입고 한 건·원 물량을 확인한다.

초기 신규 Python 6 PASS(3.50초), TypeScript/Vite build PASS, 프런트엔드 11 PASS, Edge 검토 5 PASS(11.2초). 전체 회귀·패키지 결과는 아래 후속 기록 및 output/sorting-tests/intake-import-*.log를 참조한다. 실제 업체 ERP·대량 원장 성능·기관 PC/네이티브 창 인수는 남아 있다. 기존 OCR 엔진·실행 정책은 변경하지 않았으며 정책 P-01~15/독립 인증 미확보에 따른 승인·지급·공식 출력 차단을 유지한다.

전체 Python `-m pytest -q`: 기존 184 + 신규 6 = 190 PASS(77.10초, 기존 경고 3개). 시스템명 공백 정리 보완 후 신규 시험 6 PASS(3.14초)를 재확인했다. `npm test`: 11 PASS, `npm run build`: PASS(기존 경고 유지). `EPMS_TEST_URL=http://127.0.0.1:18767 npm run test:e2e -- tests/browser/sorting.spec.ts`: 5 PASS(11.2초). 번들 Python과 PYTHONPATH=.deps-local;.deps-desktop, PYTHONUTF8=1을 사용하며 합성 DB만 열었다. 로그는 intake-import-pytest.log, intake-import-unit.log, intake-import-build.log, intake-import-vitest.log 및 intake-import-edge.log이다.

기능 OFF 기존 업무 Edge 9 PASS(21.0초), 최신 Lite 실행파일의 별도 합성 DB에서 검토 Edge 5 PASS(10.5초)를 확인했다. intake-import-off-edge.log와 intake-import-packaged-edge.log를 참조한다. 실제 제공 sample2에는 CSV 파일이 없음을 확인했으며 원본 Excel/PDF/HWP를 CSV 입고라고 임의 재해석하지 않았다. 이번 CSV 검증은 합성 원본 기준이다. 패키지 검증은 로컬 서버/브라우저 범위이며 네이티브 창·현장 ERP 인수와 구분한다.
