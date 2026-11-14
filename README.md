# 통합조직 공동정산 수출실적 관리

Windows에서 실행하는 한국어 공동정산 업무 프로그램입니다. 실행 파일은 로컬 서버를 시작하고 전용 창에서 업무 화면을 엽니다. 사용 중 인터넷 연결은 필요하지 않습니다.

## 다운로드 및 실행

[최신 Windows x64 무설치 ZIP 다운로드](https://github.com/blitz82/export-calulator/releases/latest/download/EPMS-Windows-x64-Portable.zip)

다운로드한 ZIP 전체를 쓰기 가능한 로컬 폴더에 풀고 `공동정산 수출실적 관리.exe`를 실행합니다. 같은 폴더의 `WebView2`와 `_internal`도 함께 보관합니다. 배포본에 x64 WebView2 고정 런타임과 Python 런타임이 포함됩니다. 처음 실행하면 윈도우의 실행여부에 대한 경고가 뜰 수 있습니다. 무시하고 실행하시면 됩니다.프로그램 설정에서 품목명을 직접 입력합니다. 기본 데이터는 실행 파일 옆 `data/epms.db`에 저장됩니다.

개발 환경은 Python 3.12 이상과 Node.js 24를 사용했습니다.

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
| [BUSINESS_RULES](docs/BUSINESS_RULES.md) | 회원자격과 계산 기준 |
| [IMPORT_MAPPING](docs/IMPORT_MAPPING.md) | 제공 파일 분석과 가져오기 열 |
| [ARCHITECTURE](docs/ARCHITECTURE.md) | 실행 구성과 처리 흐름 |
| [DATA_MODEL](docs/DATA_MODEL.md) | 테이블과 이력 |
| [AUDIT_GUIDE](docs/AUDIT_GUIDE.md) | 실행번호와 원본 추적 |
| [DEVELOPER_GUIDE](docs/DEVELOPER_GUIDE.md) | 테스트와 배포 빌드 |
| [ERROR_CODE_REFERENCE](docs/ERROR_CODE_REFERENCE.md) | 구현된 검증 코드 |
| [DEVELOPMENT_STATUS](docs/DEVELOPMENT_STATUS.md) | 인수검사 범위와 남은 항목 |
| [HTML 매뉴얼](docs/manual.html) | 브라우저로 읽는 운영·개발 안내 |
