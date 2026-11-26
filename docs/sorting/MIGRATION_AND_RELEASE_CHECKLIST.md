> 종전 설계·검토 기록 보관 문서입니다. 아래 메뉴·작성·결재·지급 절차는 현재 제품에서 종료되었습니다. 현재 사용법은 [공동선별비 정산 안내](../SETTLEMENT_WORKFLOW.md)를 따르세요.

# 이관과 배포 검증 체크리스트

## 실행한 기술 검증

- 신규 임시 DB에 Alembic head 적용, 합성 0003 스키마 사본의 0004 업그레이드 및 재실행.
- 기존 테이블의 행·JSON·원본 바이너리 의미값 비교, SQLite backup과 복원본 비교.
- 별도 시험 DB 경로 확인, 운영 DB 미접속. 실제 운영 사본은 제공되지 않아 현장 이관 미검증.
- Windows Lite 및 Microsoft 서명 Fixed Runtime Portable 사전점검. package.ps1 -OutputRoot로 기존 release를 덮어쓰지 않는 로컬 검증 패키지 생성.
- package.json 및 lock 버전 1.3.5 유지. 새 릴리스 버전 부여·원격 push·공개 release 없음.

## 명령

PowerShell에서 PYTHONPATH는 .deps-local;.deps-desktop, PYTHONUTF8=1로 설정하고 bundled Python 경로를 -Python 인수로 전달했다.

```powershell
.\package.ps1 -Lite -Preflight -Python <python.exe>
.\package.ps1 -WebViewRuntime build/WebView2 -Preflight -Python <python.exe>
.\package.ps1 -Lite -OutputRoot build/sorting-validation-lite -Python <python.exe>
.\package.ps1 -WebViewRuntime build/WebView2 -OutputRoot build/sorting-validation-portable -Python <python.exe>
```

## 운영 및 현장 인수 잔여

실제 2027 지침·P-01~15 결재, 독립 인증·직무분리, 공식 운영전환 승인, 외부 중복원장 대사, 전체 비용·증빙 현장 UAT, 기관 회계·법무·정보보호 검토가 필요하다. 대표 스캔 원본의 전체 OCR 정확도·이월 물량·복합 LOT 현장 검증도 남아 있다. 기업 PC 방화벽·오프라인 네트워크 차단·읽기전용 폴더·업데이트 롤백·사용자 데이터 보존의 실기 인수는 별도이다. 검증되지 않은 항목을 PASS로 표시하지 않는다.

신규 ZIP은 검증 산출물이며 운영 배포 승인 상태가 아니다. 신규 의존성 없음, 기존 SBOM·고지·ZIP 검사 도구를 재사용한다. staged diff/추적·secret 검사는 .git 부재로 수행 불가하므로 원격 게시 전 Git이 있는 체크아웃에서 다시 확인한다.

검증 패키지 폴더를 실제 업무에 사용했다면 같은 OutputRoot로 다시 빌드하지 않는다. package.ps1은 지정 출력 루트 안의 대상인지 확인하고, 기존 패키지에 data 폴더 또는 SQLite DB가 있으면 PyInstaller의 폴더 재생성 전에 중단한다. 새 OutputRoot로 빌드하고 기존 자료는 기관 백업·복원 절차로 보존한다. 이 보호의 범위는 data 폴더/통상 SQLite 파일이며 임의 사용자 파일의 전체 백업을 대신하지 않는다. 합성 data 보존 중단과 정상 preflight를 시험했다.
