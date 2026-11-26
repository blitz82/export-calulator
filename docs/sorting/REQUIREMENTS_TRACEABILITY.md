> 종전 설계·검토 기록 보관 문서입니다. 아래 메뉴·작성·결재·지급 절차는 현재 제품에서 종료되었습니다. 현재 사용법은 [공동선별비 정산 안내](../SETTLEMENT_WORKFLOW.md)를 따르세요.

# 요구사항 추적표

2026-11-23 최신 보완: 신규 구현: epms/sorting_paper.py, sorting_api의 paper-cases/paper-bank-results, web/sorting-paper.tsx. tests/test_sorting_paper.py 10개와 tests/browser/paper.spec.ts가 검증한다. 기존 전자승인/자동지급/official 출력 차단은 유지하며 외부 사실의 증빙 기록과 구분한다. 상세는 [PAPER_WORKFLOW.md](PAPER_WORKFLOW.md), 최신 검증은 IMPLEMENTATION_STATUS.md를 따른다.

원본 38개 규칙을 추출한 기준선. 부분 구현은 PASS로 표시하지 않는다.

| 규칙 | 요구사항 | 지위 | 구현·시험 | 상태 |
| --- | --- | --- | --- | --- |
| BR-101 | 해당 품목 통합조직 회원과 지정 공동선별장의 실수출·공동정산 물량 확인. 지침상 지원대상·계약요건을 적용. | 현행 / P-01 | epms/sorting.add_facility; sorting_records.append → tests/test_sorting.py::test_versioned_contract_settlement_and_certificate | POLICY_PENDING |
| BR-102 | 회원사·시설·운영사업자·신청자·생산자·수출업체를 별도 ID로 관리. 동일 사업자의 복수 역할, 복수 시설, 복수 거래업체 허용. | 개발 | epms/sorting.facilities/batches; sorting_records; SortingOperations → tests/test_sorting.py::test_T08_T14_T18_guards_scope_snapshot | IN_PROGRESS |
| BR-103 | 회원 가입/탈퇴 및 시설 지정/해제의 유효기간·증빙 이력 관리. 작업일/선적일 등 적격판정 기준일은 정책으로 설정. | 개발 / P-02 | epms/sorting.add_facility/validate_facility; sorting_facility_review.revise; store.memberships → tests/test_sorting.py::test_T08_T14_T18_guards_scope_snapshot; tests/test_sorting_governance.py | POLICY_PENDING |
| BR-104 | 계약 당사자·품목·기간·약정량·규격·가격·공제·지급기한·클레임 조건 및 개정 이력 관리. 표준계약 동등성 확인 가능. | 개정안 / P-03 | epms/sorting_records.append CONTRACT; sorting_contract_review; web/sorting-operations.tsx → tests/test_sorting.py::test_versioned_contract_settlement_and_certificate; tests/test_sorting_governance.py; tests/browser/sorting.spec.ts | POLICY_PENDING |
| BR-105 | 연·월간 계획 및 변경 이력 관리. 긴급 작업·계획 초과는 자동 불인정하지 않고 정책 및 소명에 따라 심사. | 개발 / P-05 | epms/sorting_records.append PLAN; sorting_api.revise_record → tests/test_sorting.py::test_versioned_contract_settlement_and_certificate; tests/test_sorting_governance.py | IN_PROGRESS |
| BR-201 | 입고/선별/출고 원본 단위, 수량, kg 환산값 및 환산 근거 보존. 단위 미확정은 자동정산 보류. | 개발 | epms/sorting.kg; sorting_production.intake → tests/test_sorting.py::test_numeric_rejection; test_production_shared_ledger | IN_PROGRESS |
| BR-202 | 수출 적합량, 내수, 비상품, 감모, 재고를 구분. 재선별·분할·병합 시 원 LOT와 연결하고 중복 계량 방지. | 개발 | epms/sorting_production.transform/ledger; web/sorting-production.tsx → tests/test_sorting.py::test_production_shared_ledger; tests/test_sorting_production.py; tests/browser/sorting.spec.ts | IN_PROGRESS |
| BR-203 | 기존 ERP 작업번호 사용 우선. 없으면 시설별 작업 ID 생성. 출고와 작업의 다대다 연결 및 잔량 관리. | 개발 | epms/sorting.add_batch/capacity; sorting_production.dispatch/ledger; web/sorting-allocation-editor.tsx → tests/test_sorting.py::test_production_shared_ledger; tests/test_sorting_production.py; tests/test_sorting_governance.py | IN_PROGRESS |
| BR-204 | 기본형은 지정 시설·공동선별일·품목·신고번호·신청량·객관적 증빙을 등록하고 실물 연결은 검토자가 확인. 자료 미충족 시 보류. | 개발 / P-04 | epms/sorting.add_batch; sorting.commit_import; sorting_intake_import.preview/commit; web/sorting-intake-import.tsx → tests/test_sorting.py::test_import_commit_atomic_idempotent; tests/test_sorting_intake_import.py; tests/browser/sorting.spec.ts | IN_PROGRESS |
| BR-205 | 선별일, 출고일, 신고일, 출항예정일, 실제 적재·선적일, 비용 귀속월과 지급일을 각각 보존. 재고 이월 및 교차월 허용 여부는 정책화. | 개발 / P-06 | epms/sorting_api.Batch/Intake/Dispatch; sorting_evidence_review.assess → tests/test_sorting.py::test_declared_and_actual_months_stay_separate_without_automatic_attribution | POLICY_PENDING |
| BR-206 | 동일 물량을 증명하는 연결이 없는 월별 총량 비교는 참고검증으로만 사용. 수량 차이·이월·내수전환 등 사유 기록. | 개발 | epms/sorting.reserve; sorting_documents.group_pages → tests/test_sorting.py::test_T06_reservation_idempotency_and_release | IN_PROGRESS |
| BR-207 | 공동선별비 지원 가능한 물량은 검증된 선별·출고·수출 배분의 교집합으로 제한. 계약 및 정산요건은 승인 정책 적용. | 개발 / P-03 | epms/sorting.reserve; sorting_production.batch_committed → tests/test_sorting.py::test_T06_concurrent_overflow; test_production_shared_ledger | IN_PROGRESS |
| BR-301 | 공식 신고번호 원문과 비교용 정규화값을 동시 저장. 공백·하이픈 차이를 정규화하되 검증문자 누락·충돌은 후보로만 처리. | 개발 | epms/sorting.normalize_number/candidates; web/sorting-allocation-editor.tsx → tests/test_sorting.py::test_M1_candidates_versions_and_correction_delta | PASS |
| BR-302 | 정산 물리 키는 유효 신고번호+품목란+신고 개정버전. 정정본은 동일 논리 키의 신규 버전으로 처리하며 새 물량으로 추가 금지. | 개발 | epms/sorting.physical → tests/test_sorting.py::test_T08_T14_T18_guards_scope_snapshot | IN_PROGRESS |
| BR-303 | 수출화주·대행자·제조자 등 역할별 사업자 확인. 기존 회원사 귀속실적 합계와 별개로 신고란 순중량을 1회만 사용. | 개발 | epms/sorting.physical; existing rules unchanged → tests/test_sorting.py::test_T06_reservation_idempotency_and_release; tests/test_epms.py | IN_PROGRESS |
| BR-304 | 실제 수출이행일·상태의 근거자료 등록. 수출이행 표기와 출항예정일만으로 실제 선적일 자동 확정 금지. 운송서류(AWB/B/L)는 보조근거. | 개발 / P-06 | epms/sorting_evidence_review.record/assess; web/sorting-evidence-review.tsx → tests/test_sorting.py::test_auxiliary_transport_cannot_confirm_actual_date; test_link_review_idempotent_history_revoke_and_export | POLICY_PENDING |
| BR-305 | 신고 품목란과 선별·출고의 다대다 매핑. 원본 유효 순중량·작업 잔량을 초과하는 배분 및 중복 승인 차단. | 개발 | epms/sorting.reserve/capacity/line_transition; web/sorting-allocation-editor.tsx; web/sorting-review-lines.tsx → tests/test_sorting.py::test_M1_capacity_multiline_and_stale_version; tests/browser/sorting.spec.ts | PASS |
| BR-306 | 제36번 순중량 원본을 Decimal/고정소수점으로 보존. OCR·Excel 차이 표시, 임의 정수화 금지. | 개발 / P-07 | epms/sorting.decimal/kg; sorting_books.preview; sorting_reconciliation.compare → tests/test_sorting.py::test_numeric_rejection; test_sorting_reconciliation.py | PASS |
| BR-401 | 비용 발생액·적격비용·배부비용·기준비용·신청액·인정액·지급액을 별도 저장. 증빙 기재비용을 실제비용으로 자동 해석 금지. | 개발 | epms/sorting.expenses/expense_allocations; sorting_cost_review.review; web/sorting-cost-review.tsx → tests/test_sorting.py::test_T03_failed_retry_and_T11_T13_expense_pool; tests/test_sorting_cost_review.py; tests/browser/sorting.spec.ts | IN_PROGRESS |
| BR-402 | 시설·귀속월·비용항목·가명 ID별 비용 원장. 수출 선별인건비 분리 및 급여·정상 이체 대사. [S6] 제외비용 별도 표시. | 개발 | epms/sorting_finance.add_expense/paid_cost; sorting_cost_review.review → tests/test_sorting.py::test_T03_failed_retry_and_T11_T13_expense_pool; tests/test_sorting_cost_review.py | IN_PROGRESS |
| BR-403 | 지급의뢰, 실패, 정상, 재이체, 취소 상태 분리. 원거래와 재이체 연결 후 실제 지급액을 한 번만 인정. | 개발 | epms/sorting_finance.record_payment → tests/test_sorting.py::test_T03_failed_retry_and_T11_T13_expense_pool | PASS |
| BR-404 | 수출 선별인건비와 내부인력 인건비 분리 불가 시 해당 비용 정산불가. 내수·포장재·운송비 제외. 배부는 객관적 근거 및 승인 기준 필요; 물량비율만으로 인정 금지. | 현행 패키지 / P-10 | epms/sorting_finance.eligibility → tests/test_sorting.py::test_T03_failed_retry_and_T11_T13_expense_pool | PASS |
| BR-501 | 수출업체의 생산자단체 구매대금과 생산자단체의 농가 판매대금·공제·실정산금을 구분. 계약 ID 및 출하자료와 연결. | 개정안 / 개발 | epms/sorting_records.append; sorting_contract_review; web/sorting-contract-review.tsx → tests/test_sorting.py::test_versioned_contract_settlement_and_certificate; tests/test_sorting_governance.py | IN_PROGRESS |
| BR-502 | 농가별 판매금액−선별비−기타공제−수수료±정산 조정액=실정산금. 지급내역과 대사하고 미도래·미지급·분쟁·완료 구분. | 개발 / P-11 | epms/sorting_records.append PRODUCER_SETTLEMENT; sorting_dashboard.dashboard; web/sorting-dashboard.tsx → tests/test_sorting.py::test_versioned_contract_settlement_and_certificate; tests/test_sorting_dashboard.py | POLICY_PENDING |
| BR-503 | 농가 선별비 부담과 정부지원금은 별도 계정. 환급·차감·공동기능 재투자는 승인된 정책에 한해 산정. | 정책 P-12 | epms/sorting_records.append subsidy/refund=null → tests/test_sorting.py::test_versioned_contract_settlement_and_certificate | POLICY_PENDING |
| BR-601 | 전체 물량에서 이번 신청분·타 사업 예약/신청/승인/지급분·미신청 잔량을 구분. 동일 물량 부분신청 허용, 중복 금지. | 개발 | epms/sorting.reserve/transition/line_transition → tests/test_sorting.py::test_T06_reservation_idempotency_and_release | IN_PROGRESS |
| BR-602 | 신고란별 배분 ID와 비용증빙 거래 ID로 중복검사. 중복 차감은 서로 겹치는 구간의 합집합으로 계산하여 이중 차감 방지. | 개발 | epms/sorting_finance.form_amount/allocate_cost → tests/test_sorting.py::test_T20_union_and_blank; test_T03_failed_retry_and_T11_T13_expense_pool | IN_PROGRESS |
| BR-603 | 신청 또는 예약 시 잔량을 원자적으로 잠금. 반려·철회 시 해제, 지급 후 환수 시 실제 정책에 따라 잔량 재개방 승인. | 개발 / P-13 | epms/sorting.reserve/transition BEGIN IMMEDIATE → tests/test_sorting.py::test_T06_concurrent_overflow; test_row_reject_only_releases_selected_portion | PASS |
| BR-604 | 외부 사업 원장이 없으면 중복 없음으로 자동 확정하지 않음. 미조회·업체확인·원장대사·중복확정 상태 분리. | 개발 | epms/sorting.reserve UNKNOWN; sorting_records EXTERNAL_LEDGER → tests/test_sorting.py::test_T08_T14_T18_guards_scope_snapshot | POLICY_PENDING |
| BR-605 | 2026 패키지 실비 검토액 min(C,Q×U). 지원단가는 한도 단가. 2027 산식·보조율·절사·한도단위 미승인 시 모의계산만. | 현행 패키지 / P-07~10 | epms/sorting.reference_amount; sorting_finance.calculate_strategy; sorting_policy_review → tests/test_sorting.py::test_T09_T10_T19_null_zero; test_synthetic_strategy_rate_semantics; tests/test_sorting_governance.py | POLICY_PENDING |
| BR-606 | 승인 전 유효 정책·배정잔액·실수출·거래·적격비용·중복·필수 증빙 검증. 사무국 발급 공동정산 확인서 필수(물량 및 중복 여부). 미충족 행 분리. | 현행 패키지 / 개발 | epms/sorting.operation_guard/review; sorting_evidence_review.certificate_assessment → tests/test_sorting.py::test_concurrent_certificate_reservations_cannot_exceed_quantity; test_source_revision_scope_and_tamper | POLICY_PENDING |
| BR-607 | 공식 집행시스템의 실제 지급내역을 등록·대사. EPMS 계산 결과를 자체 지급 권한으로 취급하지 않음. | 개발 | epms/sorting.operation_guard; sorting_group_review.calculate; web/sorting-group-review.tsx → tests/test_sorting.py::test_T08_T14_T18_guards_scope_snapshot; tests/test_sorting_governance.py | POLICY_PENDING |
| BR-608 | 승인 시 신청행·수출 원장 버전·정책 버전·증빙 해시·산식·검토자/승인자 스냅샷 보존. 수정은 신규 버전으로 처리. | 개발 | epms/sorting.reserve; sorting_snapshots.capture/save/get; web/sorting-snapshots.tsx LOCAL_REVIEW_ONLY → tests/test_sorting.py::test_T08_T14_T18_guards_scope_snapshot; tests/test_sorting_governance.py | IN_PROGRESS |
| BR-609 | 신고 정정·취소·가격/비용 수정 시 영향분 재계산, 차액조정·환수안 생성. 이미 승인된 이력은 삭제 금지. | 개발 / P-14 | epms/sorting.review; sorting_snapshots.capture/compare/candidate; web/sorting-snapshots.tsx → tests/test_sorting.py::test_T08_T14_T18_guards_scope_snapshot; tests/test_sorting_governance.py | IN_PROGRESS |
| BR-610 | 신청서·확인서·세부내역 및 감사 출력. 2026.9.1 별지11호 B항목 생산유통통합조직, 공동선별비 명칭 반영. 2027 양식 미확정 시 검토용. | 개발 | epms/sorting_books.report; sorting_print.render; sorting_api.form_simulate → tests/test_sorting.py::test_print_form_escapes_input_keeps_unknowns_and_blocks_official; tests/browser/sorting.spec.ts | IN_PROGRESS |
| BR-405 | 수출 선별인건비만 적격 후보. 미분리·내수·포장재·운송비 제외; 용역비·보험료·수당 등 판단 미확정 항목은 보완/보류. 비용 원본은 삭제하지 않음. | 현행 패키지 / P-10 | epms/sorting_finance.eligibility → tests/test_sorting.py::test_T03_failed_retry_and_T11_T13_expense_pool | PASS |
| BR-611 | 가이드라인·불공정거래·수출질서·안전성 위반은 심의 사건으로 등록. 확정 처분의 근거·대상·기간·범위만 적용. 조사·심의 중 자동 차감 금지. | 현행 패키지 / P-15 | epms/sorting_records.append SANCTION; sanction_candidate TEST → tests/test_sorting.py::test_T16_T17_sanction_scope | POLICY_PENDING |
| BR-612 | 2026 기준 프로파일은 REFERENCE_ONLY, 2027 DRAFT는 SIMULATION_ONLY. 서버에서 승인확정·지급결과 신규 등록·공식 지급용 출력 차단. 과거 증빙은 읽기/검토용 이력 등록만 허용. 운영 전환은 별도 승인·독립 권한·정책 전체 확인. | 개발 / P-01~15 | epms/sorting.operation_guard; sorting_api.install; capability OFF → tests/test_sorting.py::test_off_existing_contract; test_T08_T14_T18_guards_scope_snapshot | PASS |

PASS는 해당 기술 규칙의 합성 시험 범위이며 단계 전체 통과·정책 승인·실제 지급 적격을 뜻하지 않는다. IN_PROGRESS 항목은 미구현·추가 시험이 남아 있다. 각 P 상태와 영향은 POLICY_REGISTER.md를 참조한다.

## 단계별 남은 인수조건

M1: 다대다·증빙 연결·두 Excel 자료 대사 및 A4 검토 서식 출력을 구현했다. 제공 두 Excel 112건 중량 일치이나 모두 품목란 미확인이다. 남은 항목은 실제 스캔 PDF/신고/비용의 전수 대사와 공식 정책·서식·현장 인수이며 로컬 검토기록을 운영 승인으로 승격하지 않는다.
M2: 비용별 정상/실패/재이체·신청 배부·해제·원본 무결성 대사 화면을 구현했다. 실제 비용/은행 원장 전수 대사·예산 그룹 누적·정책 승인/독립 인증·현장 인수와 확정본/자동 조정 검토가 남아 있다.
M3: 복수 원물/잔량·기간 통계와 범용 입고 CSV 매핑/원본/재제출 이력을 구현했다. 실제 ERP별 전용 매핑·이월 재사용·혼합 스캔/현장 검증·실제 이행 분모/정책 기준은 남아 있다.
M4: 실제 운영 사본 migration, 기업 PC 오프라인·권한·업데이트/실행 인수, 최종 UAT. Git 이력·staged PII 점검은 .git 부재로 BLOCKED.
# 지급 준비 검토 후속 연결 (2026-11-23)

M2 검토 기능을 sorting_payment_preparation.py / sorting_api.py / web/sorting-payment-preparation.tsx / tests/test_sorting_payment_preparation.py / tests/browser/payment-preparation.spec.ts로 확장했다. 기준 명세 고정·회원별 수동 준비·개정/중복 활성 통제·회원 전용 검토 출력을 제공한다. 실제 지급으로 해석하지 않으며 BR-612 운영 차단과 B1~B4의 미해결 선행조건을 유지한다. 최신 시험 결과는 IMPLEMENTATION_STATUS.md를 따른다.
# 지급 준비 종합 점검 후속 연결 (2026-11-23)

sorting_readiness.py / sorting_api.py / web/sorting-readiness.tsx / tests/test_sorting_readiness.py 및 tests/browser/payment-preparation.spec.ts에서 현재 신청·계약·귀속·비용·고정 명세·준비의 읽기 전용 종합 대조를 제공한다. 신규 완료 상태/운영 권한을 생성하지 않고 BR-612와 기존 B1~B4 미해결 선행조건을 유지한다. READINESS_REVIEW.md와 IMPLEMENTATION_STATUS.md를 따른다.
