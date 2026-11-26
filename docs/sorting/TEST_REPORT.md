> 종전 설계·검토 기록 보관 문서입니다. 아래 메뉴·작성·결재·지급 절차는 현재 제품에서 종료되었습니다. 현재 사용법은 [공동선별비 정산 안내](../SETTLEMENT_WORKFLOW.md)를 따르세요.

# 시험 기록

최신 분석·순차 보완과 검증 범위는 [구현 수준과 잔여 순서](IMPLEMENTATION_AUDIT_AND_ORDER.md)를 따른다. 이전 결과는 [비용 대사](COST_RECONCILIATION.md), [입고 CSV](INTAKE_CSV_IMPORT.md), [기간 통계](REVIEW_DASHBOARD.md), [생산 원장](PRODUCTION_REVIEW.md), [PDF 검토](PDF_PAGE_REVIEW.md)에 보존한다.

## 기존 기준선

실행일 2026-11-22, Windows PowerShell, Node 기존 설치 및 bundled Python. PYTHONPATH=.deps-local;.deps-desktop, PYTHONUTF8=1. WindowsApps python 별칭은 실행 불가하여 bundled Python을 사용했다. .deps는 py.py 접근 오류가 있어 경로에서 제외했다.

| 명령 | 결과 |
| --- | --- |
| bundled-python -m pytest -q -s | 종료 0, 107 PASS, 경고 3, 68.35초 |
| npm test | 종료 0, 2 PASS |
| npm run build | 종료 0, 기존 chunk 크기 및 use client 경고 |
| bundled-python launch.py --port 18766 --db output/sorting-tests/baseline-e2e.db --no-browser | 별도 합성 DB 서버 |
| npm run test:e2e | 종료 0, Edge 9 PASS, 20.8초 |

운영 DB에 연결하지 않았다. sample2 원본은 쓰기·업로드하지 않았다. 실제 지급액·정책 효력 검증을 의미하지 않는다.

## 신규 구현 및 회귀

전체 Python 최종 회귀는 143 PASS, 경고 3, 종료 0, 68.12초이다. 기준선 107개를 유지하고 신규 합성 시험 36개를 추가했다. 다운로드 헤더·큰 소수값 중간 정밀도·다단 머리글 보완까지 포함한 결과다. npm test 2 PASS, TypeScript/Vite build PASS이다. 기존 chunk/use client 경고는 기준선과 동일 종류이다.

| 검증 | 명령·시험 경로 | 실제 결과 및 증거 |
| --- | --- | --- |
| 기존 기능 OFF | EPMS_TEST_URL=http://127.0.0.1:18766 npm run test:e2e -- tests/browser/workflow.spec.ts | 9 PASS, 21.9초; output/sorting-tests/final-e2e-off.log |
| 기존 기능 ON | EPMS_TEST_URL=http://127.0.0.1:18767 npm run test:e2e -- tests/browser/workflow.spec.ts | 9 PASS, 20.9초; output/sorting-tests/final-e2e-on.log |
| 신규 검토 UI | tests/browser/sorting.spec.ts | 2 PASS, 4.4초. 모의계산·실제 예약·Excel 다운로드 시험. 최초 파일명 실패를 신규 Content-Disposition 계약 수정으로 해결; 최종 로그 final-e2e-sorting.log |
| 수량·정밀도 | test_T01_T02_T04_reference; test_numeric_rejection | 3개 사례 금액 재현, NaN·음수·소수점·범위 거절 |
| null·0·실비 한도 | test_T09_T10_T19_null_zero | 미확인 비용 null과 확인 0 구분, min(C,Q×U) 정확 비교 |
| 경쟁 예약·부분 해제 | test_T06_concurrent_overflow; test_row_reject_only_releases_selected_portion | 초과 차단, 재전송·요청키 충돌·선택 행 해제·rollback 검증 |
| 정책·조직·snapshot | test_T08_T14_T18_guards_scope_snapshot; test_off_existing_contract | 직접 API/관리자/배치/공식 출력 차단, 다른 조직 ID 거절, 원본 변경 검출 |
| 비용·재이체 | test_T03_failed_retry_and_T11_T13_expense_pool | 실패액 제외, 성공 재이체 1회, pool 초과·중복 차단, 비용 제외·보류 |
| 계약·농가정산·제재 | test_versioned_contract_settlement_and_certificate; test_T16_T17_sanction_scope | 버전 불변·실정산 대사, 심의 자동차감 없음·합성 확정범위 시험 |
| 신청식·시험 산식 | test_T20_union_and_blank; test_synthetic_strategy_rate_semantics | B~E 동일 portion ID 합집합·공란 보류, 단가 성격별 보조율·예산0·중복차감·감소분 검토 |
| 생산 교차 원장 | test_production_shared_ledger | 원물 초과·중복 투입 차단, 확장 출고와 기본 예약 교차 중복 차단 |
| 복합 문서·150쪽 | test_compound_grouping_no_net_gross_or_reissue_sum; test_150_page_compound_cache_recovery | 갑을지/재발급 중복 미합산·AWB gross 미사용, 150쪽 합성 전자 PDF 5청크 및 재사용 PASS |
| 이관·복원 | test_full_old_schema_semantic_backup_migration; test_migration_backup_restore | 합성 0003 DB backup 사본 0004 이관·기존 행/바이너리 의미값 동일·백업 복원 PASS |

원본 Excel 대사는 tools/sorting_sample_check.py로 수행했으며 두 프로파일 모두 112건·126,524kg이다. 첫 단순 집계의 2배 불일치는 합계행 포함이 원인임을 원본 수식으로 확인하고 합계행 제외 규칙으로 해결했다. 원본이나 기대값을 수정하지 않았다. 세부 결과는 비공개 output/sorting-private/sample-check.json이다.

## 미검증 범위

실제 2027 지급액·정책 승인, 실제 비용/급여/이체 전체 대사 및 전체 스캔 OCR 정확도, 모든 누락·회전·교차기간·복수 사용자 조합, 실제 기관 DB 이관, 강제종료 복구 실기·기업 PC 오프라인 UAT는 미검증이다. 합성 150쪽 시험은 실제 스캔 인식률의 근거가 아니다. 전체 마스터 인수조건 PASS로 해석하지 않는다.

## Windows 검증 패키지

Lite 및 Portable의 package.ps1 사전점검·PyInstaller·ZIP/SBOM 검사가 모두 통과했다. Microsoft Fixed Runtime 서명과 x64를 기존 검사기로 확인했다. Lite 69개 입력/134개 파일 hash, Portable 70개 입력/173개 파일 hash, 양쪽 모두 로컬 고지 없는 입력 0개, Vite 관찰 npm 패키지 9개이다. 신규 의존성을 추가하지 않았다. 실시간 온라인 취약점 조회는 수행하지 않았다.

최신 Lite와 Portable exe를 각각 --no-browser와 명시적 합성 DB 경로로 실행하여 로컬 API 시작, 검토 ON, 지급 필드 null, 큰 소수값의 정확한 중간 곱 및 Excel 보조 머리글 제외를 확인했다. Lite 기본 OFF 실행도 별도로 통과했다. 실제 네이티브 창·기업 PC·오프라인 네트워크 차단 실기는 미검증이다. 증거: output/sorting-tests/lite-smoke-final.log, portable-smoke-final.log, lite-smoke-off.log, final-lite-build.log, final-portable-build.log.

tools/sorting_package_scan.py는 앱 data 폴더/DB/DOCX/HWP/원본 hash 매치를 검사한다. 벤더 라이선스의 data 하위경로는 사용자 DB가 아닌 필수 고지임을 구분한다. 실제 제공 자료 hash 매치 및 민감 경로는 0건이다. 기존 rules/store/books/submission/OCR 및 web/api.ts는 기준선 SHA-256과 동일하다. Git의 추적·staged secret 검사는 .git 부재로 BLOCKED이다.

## M1 후속 인수 보완

후속 변경 기준 Python 전체 150 PASS, 종료 0, 65.62초(기존 107 + 공동선별 43)이다. npm 11 PASS, TypeScript/Vite build PASS. 결과는 output/sorting-tests/m1-next-regression.log, m1-next-frontend.log, m1-next-build.log에 남겼다. 이전 절의 143/2는 1차 구현 당시 기준이며 이번 변경 이후 최신 수치는 이 절을 따른다.

새 시험 test_M1_capacity_multiline_and_stale_version은 600+400kg 신청, 보완 시 예약 유지, stale 행 전이 거절, 부분 반려 후 400kg 잔량 및 검토 Excel의 상태·해제·날짜 시트를 확인한다. test_M1_candidates_versions_and_correction_delta는 검증문자 누락 후보검색·복수 유효본 거절·1,000→500kg 정정 후 기존 600kg 예약에 대한 초과 100kg/차이 −500kg을 확인한다. test_M1_dates_are_separate_and_period_warnings_preserve_original 및 지정기간 경계 4개 시험은 실제 수출이행 미확인·교차월·출고 역전과 시작/종료일 포함 경계를 검증한다. 새로운 9개 프런트엔드 시험은 소수점 합계·큰 값·음수/NaN/자릿수 거절을 검증한다.

신규 Edge 2개 시나리오에서 다대다 목록의 탭 이동 보존, 60+40kg 일괄 예약, 행별 보완과 40kg 부분 반려, 현재 예약 60kg/원래 신청 100kg 표시 및 Excel 다운로드를 확인했다. 원본 DB·기존 수출 API·정책 승인 상태를 변경하지 않았다. 검토용 흐름의 기술 시험이며 실제 정책·현장 인수 완료를 의미하지 않는다.

최종 소스 Edge 2 PASS(4.9초), 기능 OFF 기존 Edge 9 PASS(24.3초)이다. 최신 Lite 실행 파일의 별도 합성 DB에서도 동일 신규 Edge 2 PASS(5.5초)로 확인했다. 증거는 m1-next-e2e.log, m1-next-e2e-off.log, m1-next-packaged-e2e.log이다. 기술 인수조건 중 다대다·행별 보완 UI는 완료했으며 실제 증빙 연결/대사 및 정책·현장 인수는 남아 있다.

M1 후속 소스의 Lite/Portable package.ps1 빌드 및 최신 Portable exe smoke는 PASS이다. Portable smoke는 검토 모드·고정밀 산식·Excel 보조 머리글 제외와 신규 capacity 라우트의 DTO 검증(필수 query 누락 422)을 확인했다. 증거: m1-next-lite-build.log, m1-next-portable-build.log, m1-next-portable-smoke.log. 별도 검증 폴더만 갱신했고 공개 릴리스 및 버전 번호 변경은 하지 않았다.

## M1 수출이행·확인서 연결 검증

최신 Python 전체 162 PASS, 경고 3, 종료 0, 69.36초이다. 기준선 107개와 공동선별 55개(증빙 전용 12개)를 포함한다. 신규 시험 파일은 tests/test_sorting_evidence.py이다. 정상 연결·멱등 재전송·서로 다른 요청키 본문 충돌·철회 및 이력 version·검토 Excel, 품목/회원/기간 불일치, AWB/B/L 보조자료, stale revision·조직 scope, 증빙 바이트/입력 변조·증빙 누락·확인서 개정, 동일 원본을 더 큰 물량/다른 ID로 재등록하는 우회 및 동시 연결 한도, 교차월 보존을 확인했다. 전체 회귀는 m1-evidence-regression.log, 전용 시험은 m1-evidence-unit.log에 기록했다.

프런트엔드 11 PASS와 TypeScript/Vite build PASS이며, 신규 Edge 2개 시나리오에 실제 이행 검토값·확인서 선택·대사 상태 및 과거 이력 조회를 추가했다. m1-evidence-frontend.log, m1-evidence-build.log, m1-evidence-e2e.log를 참조한다. 합성 자료 시험이며 실제 발급자/독립 검토자 인증이나 운영 적격/실제 지급액을 검증한 것이 아니다. 승인/지급·공식 출력 차단은 연결 완료 이후에도 유지되는 것을 API 시험으로 확인했다.

최신 소스 신규 Edge 2 PASS(5.9초), 기능 OFF 기존 Edge 9 PASS(21.0초)이며, 최신 Lite 실행 파일의 별도 합성 DB에서 증빙 연결·이력 조회·행별 보완·부분 반려·Excel 출력의 동일 Edge 2 PASS(5.3초)이다. m1-evidence-e2e.log, m1-evidence-e2e-off.log, m1-evidence-packaged-e2e.log에 기록했다. 원격 및 운영 DB에는 연결하지 않았다.

동일 소스의 Lite/Portable package.ps1 빌드 및 Portable exe 검토 모드 기동 smoke도 PASS이다. m1-evidence-lite-build.log, m1-evidence-portable-build.log, m1-evidence-portable-smoke.log에 기록했다. 실제 네이티브 창·기업 PC 오프라인 및 기관 자료 전수 인수는 여전히 미검증이다.

## M1 자료 대사·인쇄 서식 검증

최신 Python 전체 166 PASS, 경고 3, 종료 0, 70.49초이다. tests/test_sorting_reconciliation.py의 4개 신규 시험은 Decimal 0.4kg 차이, 공란/0/수식, 복수 후보·누락, 미확인 품목란·품명 차이, 쓰기 없는 API·조직 범위·원본 변조, CSV 수식 주입 방지, HTML escape·A4·공식 출력 차단을 검증한다. m1-forms-regression.log와 m1-forms-unit.log에 기록했다.

프런트엔드 11 PASS, TypeScript/Vite build PASS이며 신규 Edge 2 PASS(5.9초)이다. 기존 신규 시나리오에 두 Excel 업로드/시트 선택·동일 원본 경고·대사·CSV 다운로드, 60kg 예약의 인쇄 서식 iframe 미리보기·닫기를 추가했다. m1-forms-frontend.log, m1-forms-build.log, m1-forms-e2e.log를 참조한다.

파일 변경 시 이전 대사 결과를 즉시 지우는 UI 보완 후 신규 Edge 2 PASS(5.7초), 기능 OFF 기존 업무 Edge 9 PASS(20.0초)를 재확인했다. CSV의 원본 hash·시트명도 텍스트 접두로 보존하도록 보완한 뒤 대사/인쇄 단위시험 4 PASS를 재확인했다.

tools/sorting_print_qa.mjs로 합성 인쇄 HTML을 Edge에서 PDF로 저장했고 PDFium으로 3쪽을 렌더링해 모두 시각 확인했다. 표·문자 잘림이 없고 미확인/미확정과 검토용 표시가 보존된다. 실제 기관 양식의 제출 인수 또는 네이티브 Excel/프린터 실기 인수와 동일하지 않다. QA 파일은 output/sorting-tests/form-preview.* 및 form-page-*.png이며 내부 검증용이다.

실제 제공 자료 대사 범위는 SAMPLE_RECONCILIATION_REPORT.md에 별도로 기록했다. 112건 표 간 중량 일치는 신고 원본/실물 검증이 아니다. 스캔 PDF 335쪽과 HWP 본문 전수 대사, 실제 지급액 및 정책 승인 검증은 완료하지 않았다.

최신 Lite exe의 별도 합성 DB에서도 동일 신규 Edge 2 PASS(6.0초)이다. 대사·CSV 저장 및 A4 인쇄 서식 iframe이 실제 패키지에서 표시되는 것을 확인했다. m1-forms-packaged-e2e.log를 참조한다. 기능 OFF 기존 Edge는 9 PASS이며 기존 rules/store/books/submission/OCR 및 web/api.ts 8개 보호 파일의 기준선 hash도 그대로이다.

동일 소스의 Lite/Portable 빌드 및 Portable exe 검토 모드 기동 점검도 PASS이다. m1-forms-lite-build.log, m1-forms-portable-build.log, m1-forms-portable-smoke.log에 기록했다. package.ps1에는 출력 경로 검증과 기존 data 폴더/SQLite DB 발견 시 재빌드 중단 보호를 추가했다. 합성 data/keep.txt를 보존하며 중단하는 시험과, 정상 Lite/Portable preflight를 확인했다. 네이티브 창/프린터·기관 PC·실제 사용자 업데이트 이관 전체를 인수한 것은 아니다.
