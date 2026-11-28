# 통합조직 공동정산 수출실적 관리

Windows에서 실행하는 한국어 공동정산 업무 프로그램입니다. 실행 파일은 로컬 서버를 시작하고 전용 창에서 업무 화면을 엽니다. 사용 중 인터넷 연결은 필요하지 않습니다.

## 실행

`EPMS-Windows-x64-Lite-v2.0.4.zip` 전체를 쓰기 가능한 로컬 폴더에 풀고 `프로그램 시작.exe`를 실행합니다. 같은 폴더의 `_internal`도 함께 보관합니다. 실행 시 PC의 WebView2 Runtime을 확인합니다. 설치 안내가 나오면 [Microsoft WebView2 다운로드](https://developer.microsoft.com/microsoft-edge/webview2/#download)에서 Evergreen Runtime을 설치한 뒤 다시 실행하세요. 기본 데이터는 실행 파일 옆 `data/epms.db`에 저장됩니다. 경량 배포본은 `.\package.ps1 -Lite`로 만듭니다.


개발 환경은 Python 3.12 이상과 Node.js 24를 사용했습니다.

별도 런타임 설치가 어려운 PC에서는 [GitHub 릴리스](https://github.com/blitz82/export-calulator/releases/latest)의 Windows x64 무설치판(Portable)을 사용하세요. 무설치판은 ZIP 전체를 풀고 `프로그램 시작.exe`를 더블클릭합니다. 두 배포본 모두 다운로드한 ZIP의 **속성 → 일반**에 **차단 해제**가 표시되면 적용한 뒤 압축을 푸세요.

```powershell
python -m pip install -r requirements-dev.txt
npm ci
npm run build
.\run.ps1 -NoBrowser
```

소스에서 전용 창을 실행하려면 `build/WebView2`에 공식 x64 고정 런타임을 푼 후 `run.ps1`을 실행합니다.

| 문서 | 내용 |
| --- | --- |
| [USER_MANUAL](docs/USER_MANUAL.md) | 업무 순서와 입력 방법 |
| [ADMIN_MANUAL](docs/ADMIN_MANUAL.md) | 정책, 백업, 복원 |
| [MEMBER_CONTRACTS](docs/MEMBER_CONTRACTS.md) | 회원사 계약 등록, PDF 근거, 회원사 연결도 |
| [SETTLEMENT_WORKFLOW](docs/SETTLEMENT_WORKFLOW.md) | 공동선별 증빙, 선 연결, 잔량·비용 배부, 참고 계산과 PDF |
| [BUSINESS_RULES](docs/BUSINESS_RULES.md) | 회원자격과 계산 기준 |
| [IMPORT_MAPPING](docs/IMPORT_MAPPING.md) | 제공 파일 분석과 가져오기 열 |
| [ARCHITECTURE](docs/ARCHITECTURE.md) | 실행 구성과 처리 흐름 |
| [DATA_MODEL](docs/DATA_MODEL.md) | 테이블과 이력 |
| [AUDIT_GUIDE](docs/AUDIT_GUIDE.md) | 실행번호와 원본 추적 |
| [DEVELOPER_GUIDE](docs/DEVELOPER_GUIDE.md) | 테스트와 배포 빌드 |
| [ERROR_CODE_REFERENCE](docs/ERROR_CODE_REFERENCE.md) | 구현된 검증 코드 |
| [DEVELOPMENT_STATUS](docs/DEVELOPMENT_STATUS.md) | 인수검사 범위와 남은 항목 |
| [Smart OCR 안내](docs/OCR_REVIEW_WORKFLOW.md) | PDF 자동입력, 원본 대조, 보완 템플릿 |
| [v2.0.4 표시 방식 복원](docs/RELEASE_2_0_4.md) | 이전 글자·버튼 크기 및 연결도 매칭 복원 |
| [v2.0.3 사용성 보완](docs/RELEASE_2_0_3.md) | 계약 확인·입력 보존·가독성·목록 매칭·자동 배분 |
| [v2.0.2 변경 기록](docs/RELEASE_2_0_2.md) | 새 기능, 마이그레이션과 스캔 제약 |
| [종전 공동선별 설계 보관](docs/sorting/IMPLEMENTATION_STATUS.md) | 종료된 검토·결재·지급 절차의 역사 기록 |
| [HTML 매뉴얼](docs/manual.html) | 브라우저로 읽는 운영·개발 안내 |
