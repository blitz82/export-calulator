> 종전 설계·검토 기록 보관 문서입니다. 아래 메뉴·작성·결재·지급 절차는 현재 제품에서 종료되었습니다. 현재 사용법은 [공동선별비 정산 안내](../SETTLEMENT_WORKFLOW.md)를 따르세요.

# 기간·시설별 검토 통계 (2026-11-23)

마스터 M3의 생산단계 지표 중 등록 자료로 계산 가능한 선별 적합량·농가 정산 상태를 구현했다. M1 실제 증빙 대사 및 M3 전체 인수가 완료된 것은 아니다. .git이 없는 작업 사본이므로 기준 SHA/브랜치/HEAD 확인은 BLOCKED이다. 원격 push·release·운영 DB 적용은 수행하지 않는다.

## 조회 기준

생산 집계 범위는 확장형 INTAKE와 LOT_TRANSFORM 기록이다. 원물 투입 정보가 없는 기본형 sorting_batches를 투입량·적합량에 임의 합산하지 않는다. 기본형 작업만 있으면 확장형 작업 건수/투입량은 0이고 비율은 null이다. 이를 실제 선별실적이 없다는 뜻으로 해석하지 않는다.

공동선별비 검토 → 검토 통계에서 시작일·종료일·정산 기한 판단일·시설을 선택한다. 날짜 경계는 양 끝을 포함한다. 시설 미지정은 현재 조직 전체 시설이다. 조건 변경 시 이전 결과를 지우며 조회 실패는 오류로 표시한다. 근거 목록은 종류/ID 검색과 50행 페이지 이동을 지원한다.

입고는 입고일, 선별·재선별은 작업일, 정산은 지급기한이 조회기간에 속하는 기록을 집계한다. 입고와 작업은 서로 다른 원물 집단이므로 양자를 나눈 비율을 만들지 않는다. 선별 적합 비율의 분자는 각 작업 적합량, 분모는 각 작업 투입량이다. 재선별을 각각 포함하므로 실수출률·중복 없는 원물 수율이 아니다. 분모가 0이면 비율은 null이다. 소수 비율은 Decimal 60자리 문맥의 근사값이다.

정산은 현재 최종 개정본만 사용한다. 개정으로 지급기한이 기간 밖으로 이동해도 이전 버전을 다시 집계하지 않는다. 시설은 해당 정산에 연결된 계약 버전을 따른다. 계약 개정본으로 과거 정산을 임의 재귀속하지 않는다. as_of는 기한 경과 판단일이며 과거 원장/과거 지급상태를 복원하는 시점이 아니다. 실제 지급일·지급 증빙을 별도 검증한 결과가 아니라 현재 기록 지급액을 집계한다.

실정산금·기록 지급액을 합산하되 잔액=max(실정산금−기록 지급액,0), 초과 지급=max(기록 지급액−실정산금,0)를 건별로 별도 합산한다. 초과 지급을 다른 농가 잔액과 상계하지 않는다. 잔액>0이고 지급기한<판단일이면 기한 경과 참고 건수에 포함한다. 기한 당일은 제외하고 분쟁 건도 포함한다. 기록 상태와 잔액 0 건수는 공식 완료율·실제 지급 확인이 아니며 P-11에 따른 자동 탈락/차감도 하지 않는다.

## 근거·API와 통제

GET /api/org/{org}/sorting/review-dashboard?start=YYYY-MM-DD&end=YYYY-MM-DD&as_of=YYYY-MM-DD&facility_id=ID. 시설은 선택이다. 기존 세션·기능 OFF·조직·시설 범위를 검사한다. 일관된 SQLite 읽기 transaction에서 필요한 종류만 조회해 OCR 캐시를 읽지 않는다. 수량·금액은 정수 micro-kg/Decimal 문자열이며 집계에 float를 사용하지 않는다. 신규 테이블·migration·의존성·원본 변경은 없다. 기존 production-metrics 계약도 변경하지 않는다.

근거는 기록 ID/개정·시설·기준일·증빙 ID·집계값·기록 hash를 가진다. 정산에는 연결 계약 ID/개정/hash/증빙도 포함한다. source_set_hash는 근거 집합 변경 비교용이며 승인 snapshot·전자서명·진본보증이 아니다. 생산자 가명·계좌·OCR 원문을 응답에 추가하지 않는다.

계약 이행률·공동선별률·공식 거래정산 완료율은 분모/실제 이행/정책 근거가 부족해 null과 미확인 사유를 유지한다. 지급액도 null, operational_approval=false이다. P-01~15 미승인과 독립 인증/직무분리 부재에 따른 운영 차단은 유지한다. OCR 환경은 사용자 확인 사항이며 실행 정책과 기존 OCR 엔진을 변경하지 않았다.

## 구현·시험 추적

epms/sorting_dashboard.py, sorting_api.py, web/sorting-dashboard.tsx와 sorting.tsx에서 구현한다. BR-202/207의 작업 분모 및 BR-501/502의 정산 상태 검토를 보완하며 정책 인수 상태를 PASS로 올리지 않는다. tests/test_sorting_dashboard.py는 재선별 분모·소수 물량·빈 분모·기간/시설·조직·토큰/OFF·최종 개정본·기간 밖 개정·기한 당일·분쟁·초과 지급 비상계·큰 파생 실정산금·근거 hash·읽기 전용을 시험한다. tests/browser/sorting.spec.ts는 두 입고→선별→시설/기간 통계→근거 필터→조건 변경 시 결과 제거를 검증한다.

초기 신규 Python 8 PASS(3.71초), TypeScript/Vite build PASS. 시설 격리 시험 추가 후 전체 회귀·실행파일 결과는 아래 후속 기록과 output/sorting-tests/dashboard-*.log를 따른다. 현장 증빙·기관 PC·네이티브 창·실제 지급 검증은 별도이다.

전체 Python `-m pytest -q`: 184 PASS(75.21초, 기존 경고 3개). 신규 통계 시험 9개가 기존 175개에 추가되었다. 앱 의존성 경로 PYTHONPATH=.deps-local;.deps-desktop 및 PYTHONUTF8=1을 사용했다. `npm test`: 11 PASS, `npm run build`: PASS(기존 큰 청크/use client 경고). `EPMS_TEST_URL=http://127.0.0.1:18767 npm run test:e2e -- tests/browser/sorting.spec.ts`: Edge 4 PASS(9.2초). 각각 dashboard-pytest.log, dashboard-vitest.log, dashboard-build.log, dashboard-edge.log에 기록했다. 모두 별도 합성 DB이며 기존 데이터·제출/회원/신고 API 계약은 변경하지 않는다.

기능 OFF 기존 업무 Edge 9 PASS(20.2초), 최신 Lite exe의 별도 합성 DB에서 동일 검토/통계 흐름 Edge 4 PASS(8.7초)를 확인했다. dashboard-off-edge.log 및 dashboard-packaged-edge.log를 참조한다. 도구 sorting_package_smoke.ps1에도 신규 통계 API의 단위/운영 차단/지급액 null 검사를 추가했다. 패키지는 내부 검증용이며 정책 승인·기업 PC/네이티브 창 인수를 대신하지 않는다.
