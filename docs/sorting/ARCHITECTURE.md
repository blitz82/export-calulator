> 종전 설계·검토 기록 보관 문서입니다. 아래 메뉴·작성·결재·지급 절차는 현재 제품에서 종료되었습니다. 현재 사용법은 [공동선별비 정산 안내](../SETTLEMENT_WORKFLOW.md)를 따르세요.

# 공동선별비 아키텍처

기존 FastAPI·SQLAlchemy·SQLite WAL·Alembic·React를 유지한다. 기존 rules.calculate 및 submission 계약은 변경하지 않는다. 기존 세션 토큰은 프로세스 접근 통제이며 사용자 인증이나 직무분리가 아니다. 따라서 신규 모듈은 검토 전용이며 운영 전이는 서버에서 항상 거절한다.

기존 store.memberships(조직·사업자번호), declaration_lines(조직·행 ID·revision·JSON), import_files(조직·hash·바이트), audit_logs를 연계한다. 신규 테이블은 sorting_ 접두사로 분리한다. 시설→작업→수출배분→신청행과 비용→비용배분은 FK로 연결하고 조직 일치는 서비스에서 다시 확인한다. 계약·계획·입고·출고·농가정산·제재는 별도 버전형 기록으로 확장한다. 원본 신고는 복제하지 않고 읽기 어댑터로 조회한다.

물리 키는 공백·하이픈을 제거한 신고번호와 품목란이다. 개정버전은 새 물량이 아니다. 유효 원장 행이 복수이면 자동 선택하지 않는다. 현재 원장 ID·revision 및 데이터 hash를 확인하고 변경 시 재검토를 요구한다. 물리 상한은 조직 간 동일 신고도 공유한다. 신고번호 검증문자 누락은 후보 검색에만 사용한다.

수량은 kg 소수점 6자리까지 검증한 정수 micro-kg, 금액은 Decimal 문자열로 전송·저장한다. SQLite NUMERIC/float와 JS Number를 확정 계산에 사용하지 않는다. 과도한 자리수·NaN·무한대·음수·단위 혼합은 거절한다. 명시되지 않은 절사를 적용하지 않는다.

예약은 BEGIN IMMEDIATE 안에서 신고 및 작업의 가용량을 재조회한다. 동일 배분 ID는 예약→검토 상태에서 한 번만 차감하고 철회 시 해제한다. 클라이언트의 잔량과 승인금액은 신뢰하지 않는다. 미조회 외부 원장은 UNKNOWN을 유지한다. 검토 snapshot에는 정책·입력·원본 revision/hash와 증빙 hash를 보존하며 원장 변경은 과거 snapshot을 덮어쓰지 않는다.

신규 API는 /api/org/{org}/sorting/*이다. feature flag OFF가 기본이며 메뉴·업무 API를 숨긴다. ON이어도 operation_mode=REVIEW_ONLY, 정책 REFERENCE_ONLY/SIMULATION_ONLY이다. 승인·지급등록·공식 출력·운영 전환은 공통 서버 가드에서 차단한다. 기존 제출 API에는 가드를 적용하지 않는다.

이관은 추가 테이블만 생성하는 0004로 수행한다. 운영 DB는 열지 않는다. 기존 0003 스키마의 합성 사본을 SQLite backup API로 복사하고 기존 테이블의 행·JSON·바이너리 의미값을 비교한다. 신규 데이터가 있는 DB를 downgrade하지 않으며 변경 전 백업으로 복원한다. 첨부는 DB 바이트로 포함된다.

기존 호출 근거: api.create_app/local(73/81), organization(124), save(260), save_rows(324), import_book(392), approve_ocr(687), calculate(796), backup(896), submit(1045); store.engine_for(74), records(99), log; rules.eligible(205), attribute(210), calculate(269); ocr.stitch_continuations(697). 행번호는 M0 원본 기준이며 BASELINE_SOURCE_HASHES.json으로 식별한다.

## 구현 테이블과 참조

| 실제 테이블 | 관계 및 보존 범위 |
| --- | --- |
| sorting_facilities | org FK, 회원 사업자번호 및 지정증빙 참조·기간·revision |
| sorting_batches | org 및 facility FK, 정수 micro-kg, 원본·ERP번호·revision |
| sorting_evidence | org FK, SHA-256 unique(org,sha), 원본 바이트 및 검토 상태 |
| sorting_claims | org FK, 요청키 unique, 상태·revision·검토 snapshot |
| sorting_allocations | claim/batch/declaration FK, 전역 신고 logical_key·정수량·active·출고 참조·원본 hash |
| sorting_expenses | facility FK, 분리·귀속월·가명·금액 문자열·적격 후보/제외/보류 |
| sorting_expense_allocations | claim/expense FK, Decimal 문자열·active·unique(claim,expense) |
| sorting_records | org FK, kind·revision·append-only JSON; 계약·계획·농가정산·확인서·제재·외부원장·예산·조정 후보·입고·선별·출고·과거 비용이체・OCR청크 |

JSON 참조의 조직·종류·잔량은 서버에서 확인한다. 이것은 모든 관계가 DB FK인 모델과 동일하지 않으며 직접 DB 편집에 대한 강제 보장은 없다. 실제 운영 승인 모델과 정책 확정 후 별도의 구조화·무결성 검토가 필요하다. 생산 변환은 기존 원물만 부모로 참조하는 새 노드를 생성해 순환을 방지한다. 출고와 연결된 수출예약은 출고량 안에서만 배분하고 작업에서 두 번 차감하지 않는다.

M1 후속 화면은 sorting-allocation-editor.tsx와 sorting-review-lines.tsx로 분리했다. 클라이언트 목록 합계는 sorting-quantity.ts의 BigInt micro-kg로 표시하며 DB 저장값은 서버가 다시 계산한다. GET capacity는 declaration_id/declaration_revision/batch_id를 받아 현재 순중량·예약·가용량·초과량과 작업 잔량을 제공한다. 이 값은 조회용이며 예약 권한이나 잠금이 아니다. 여러 행 저장은 기존 POST claims 및 BEGIN IMMEDIATE를 사용하고 동일 요청키·본문을 재시도한다. 수정·행 반려는 신청 revision을 확인한다.

review의 각 lines에는 원본 snapshot 외 current_kg/quantity_delta/dates/issues/evidence_id/reason이 추가된다. 과거 snapshot은 변경하지 않는다. dates의 원장 기재 선적일은 실제 수출이행일과 분리하며 후자는 미확인 null을 유지한다. 지정기간·교차월·출고 역전은 원문 보존 검토 사항이며 2027 자격 기준일을 확정하는 규칙이 아니다. 추가 테이블/migration 없이 기존 JSON·원장 읽기와 별도 DTO 응답만 확장했다.

## 신청행 증빙 검토 이력

sorting_evidence_review.py가 sorting_records의 LINE_EVIDENCE_REVIEW를 사용한다. POST claims/{claim}/lines/{line}/evidence-review는 strict DTO의 revision/request_key/action/actual_shipped/fulfillment_evidence_id/fulfillment_kind/certificate_id/reason을 받는다. BEGIN IMMEDIATE 안에서 조직·신청·행·최신 revision을 검증하고 증빙 원본 hash, 확인서 데이터 hash, 원장 revision/hash, 시설/작업 hash와 함께 append-only 기록을 만든다. 신청 revision과 검토 이력 revision을 증가시키며 이전 검토 ID를 보존한다. GET은 해당 행의 과거 이력을 조회한다. GET certificates는 조직 범위 확인서와 개정 전 여부만 반환한다.

같은 요청키·본문은 저장 결과를 재사용하고 다른 본문은 거절한다. UI는 응답 유실 시 요청 당시 본문과 revision을 유지하여 재전송하고, 명시적 409는 자료를 다시 조회한다. RECORD는 연결값 전체의 새 버전이고 REVOKE는 과거 기록을 삭제하지 않는 철회이다. 종료/반려 행에 새 연결을 만들 수 없다.

검토 조회 시 최신 행별 기록을 기준으로 원본·입력 무결성과 확인서의 시설/품목/회원/작업기간, 실제 수출일의 선별/출고 순서를 재검증한다. 실제 수출일과 신고일의 교차월은 경고이며 귀속연도를 자동 변경하지 않는다. AWB/B/L 보조자료는 실제 수출일을 확정하지 않는다. 일부 자료만 있거나 자료가 변경되면 NEEDS_SUPPLEMENT, 모두 대사되면 LINKED_FOR_REVIEW이다. 모든 상태는 identity_assurance=LOCAL_UNVERIFIED이며 운영 승인 근거로 승격하지 않는다.

확인서 한도는 같은 원본 파일 hash를 참조하는 활성 행의 최신 연결을 한 번씩 합산한다. 조직과 확인서 ID가 달라도 동일 원본 pool을 공유한다. 원본을 재등록하거나 기재량을 키워도 동일 hash에 기록된 최소 확인물량으로 보수적으로 제한한다. 원자적 삽입 후 한도 검증 실패는 transaction 전체를 rollback한다. 이 규칙은 다수 독립 확인서가 한 파일에 들어 있는 경우를 자동 분리하는 기능이 아니며 개별 원본/범위 인수가 필요하다. 운영 정책은 여전히 미확정이다.

## 자료 간 대사와 인쇄용 검토 서식

POST reconcile-preview 및 reconcile-report는 left_id/left_sheet/right_id/right_sheet를 strict DTO로 받는다. sorting_reconciliation.from_evidence가 조직·원본 바이트 hash를 확인하고 기존 bounded preview를 재사용한다. 정규화 번호가 유일한 경우만 표의 대응을 표시하고 품목란·품명·수량 차이는 별도 상태이다. 반복 번호·결측·수식은 불확실 상태로 보존하며 Decimal로 차이를 비교한다. 비교 hash는 원본 참조와 행별 결과로 재현한다. 미리보기와 CSV 출력은 DB를 쓰지 않고 claim/physical reservation을 생성하지 않는다.

GET claims/{id}/print는 s.review의 서버 결과를 sorting_print.render로 변환한 escaped HTML이다. A4 신청서·확인서 참고 초안·신고 세부내역과 고정 검토 표시를 제공하며 공식 출력 요청은 공통 guard에서 거절한다. UI는 인증 헤더로 HTML을 받은 후 sandbox iframe에서 보여 준다. HTML은 외부 자원을 로드하지 않고 데이터 삽입에 escape를 적용한다. 신규 DB schema·의존성 변경 없이 기존 Excel 및 공식 수출실적 출력과 분리한다.

## 신규 API 계약

접두사 /api/org/{org}/sorting 아래 GET status/facilities/batches/claims/records/expenses/candidates/template, POST evidence/import-preview/import-commit/compound-preview/simulate/form-simulate/facilities/batches/claims/records/expenses/cost-payment-history/intakes/transforms/dispatches, 신청별 transition/lines/{line_id}/cost-allocation/report를 제공한다. DTO는 sorting_api.py의 strict 모델이며 수량·금액은 문자열이다. evidence는 최대 20MB raw body, 후보 검색은 query number, Excel commit은 증빙 ID·시트·시설·품목·선택 원본행으로 요청한다. 잘못된 본문 422, 잔량/버전 충돌 409, 조직 외 ID 404, 운영 시도 403이다.

POST claims의 request_key는 조직 안에서 멱등키이다. 같은 키의 다른 본문은 거절한다. 서버가 유효 원장과 작업 잔량을 다시 계산한다. review quantity는 active 예약량이며 original_quantity는 부분 반려 이전 전체량이다. review 출력 승인/지급 필드는 항상 null이다. 신청 상태와 행 상태를 분리하고 신청 revision으로 stale 요청을 거절한다. 정산 상태에는 실제 승인 상태를 만들지 않는다.

## 오류코드 매핑

SPEC E01~E09/W01~W09는 SORT_E01~SORT_E09/SORT_W01~SORT_W09로 분리한다. 기존 EPMS D/M 코드와 충돌하지 않는다. 응답 issues에는 code/field/message/severity/blocks_approval/explainable/resolution을 제공한다. 현재 검토 버전은 미확정·기술 오류를 관리자 소명으로 우회하지 않는다. 아직 모든 코드별 해결 UI와 담당자 권한 검증을 완성한 상태는 아니다.
