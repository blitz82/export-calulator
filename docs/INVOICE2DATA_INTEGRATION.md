# PHASE 3 기술 실증

검토 대상은 invoice2data v1.0.1, 커밋 `0e1ff18b88979b30a1d7aac1e9a7614840f7b1c2`다. Python 3.12 Windows x64 wheel의 PyPI SHA-256은 `96d84ef45eaad5c5e0070f7cf20274f1121594466bffb8723a671d8589f9102b`다. 공식 릴리스의 MIT 라이선스와 Manuel Riel 저작권 고지를 확인했다. 이 환경에서는 PyPI 인덱스에서 패키지를 받지 못했고 공식 wheel 서버에도 연결할 수 없어 실제 wheel 검증 및 실행 결과는 없다. [공식 릴리스](https://github.com/invoice-x/invoice2data/releases/tag/v1.0.1) · [Windows wheel과 해시](https://pypi.org/project/invoice2data/1.0.1/) · [태그의 라이선스 원문](https://github.com/invoice-x/invoice2data/blob/v1.0.1/LICENSE.md)

태그의 API는 템플릿과 입력 리더를 명시할 수 있으며 `ai_fallback=False`를 지원한다. 후속 직접 통합 시험에서는 EPMS 전용 템플릿과 단일 로컬 입력 리더를 사용하고 `required_fields`를 선언한다. 품목별 FOB는 신고 전체 합계와 별개의 필드로 유지한다. [고정 버전 API](https://github.com/invoice-x/invoice2data/blob/v1.0.1/src/invoice2data/api.py) · [고정 버전 템플릿 문서](https://github.com/invoice-x/invoice2data/blob/v1.0.1/docs/tutorial.md)

이번 증분은 EPMS 독립 구현을 보완했다. 란번호/총란수 라벨로 품목 범위를 먼저 나눈 뒤 번호를 별도로 검증한다. 번호가 비어 있거나 읽을 수 없어도 같은 범위의 HS코드·순중량·USD FOB는 검토 후보에 남는다. 합성 PDF에서 첫 품목 번호가 누락되고 다음 품목 번호가 있는 경우를 PDFium 경로로 시험했다. 두 품목의 값이 유지됐고 신고 전체 합계는 개별 품목 후보에 들어가지 않았다.

미승인 PDF를 다시 올리면 현재 추출기 버전으로 갱신한다. 같은 파일과 페이지 ID를 유지한 채 새 후보를 제공하고, 승인 전에는 원장에 반영하지 않는다. 기존 40개 PDF, 165쪽을 로컬에서 재분석해 신고번호 96개, 품목란 번호 96개, HS·순중량·FOB 각 82개의 후보 집계가 동일함을 확인했다. 필드 값과 정확도는 정답셋으로 평가해야 한다.
