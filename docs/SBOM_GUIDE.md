# EPMS SBOM 생성과 기존 ZIP 확인

현재 소스와 설치된 개발 환경의 목록은 `sbom/epms-development.cdx.json`이다. 기존 Lite와 Portable ZIP의 기준 목록은 각각 `sbom/epms-lite.cdx.json`, `sbom/epms-portable.cdx.json`이며 해당 ZIP과 함께 검증한다.

## 개발용 목록 재생성

프로젝트 폴더에서 아래 명령을 실행한다.

```powershell
npm run build
New-Item -ItemType Directory -Path build/oss-verification -Force
python -m epms.oss_inventory build/oss-verification development
python -m epms.supply_chain verify-graph build/oss-verification/sbom/epms-development.cdx.json
```

Vite는 각 JavaScript 청크의 SHA-256과 그 청크에 코드가 남은 npm 패키지 경로를 `dist/bundle-components.json`에 기록한다. 인벤토리 생성기는 설치된 npm 버전, lockfile, 청크 경로와 해시를 대조한다. CycloneDX의 `epms:observed-in-build`는 이 근거 또는 PyInstaller 분석에서 관찰한 구성요소를 표시한다. 관찰되지 않은 항목도 런타임 의존성 또는 빌드 입력 목록에 포함될 수 있다. 도구는 Python 설치 메타데이터와 npm lockfile을 사용해 구성요소 참조와 의존 관계를 생성한다. PDFium은 Python 바인딩과 별도 버전으로 표시한다. [CycloneDX 1.6 JSON 명세](https://cyclonedx.org/docs/1.6/json/)

의존 그래프 검사는 중복 참조와 끊긴 참조를 검출한다. Python 전이 의존성은 해당 호스트에서 기본 조건이 충족된 설치 버전을 수집하며 모든 선택적 extras와 배포 대상의 재설치 검증은 별도로 필요하다. 입력 지문에는 순수 Python 요구사항 파일, npm lockfile, OCR 템플릿을 포함한다. 패키징 입력 변경 시 의존성, 원문 고지, 버전 및 보안 공지를 다시 확인한다.

## 기존 배포 ZIP 확인

```powershell
python -m epms.supply_chain verify-zip "<Lite ZIP>" lite sbom/epms-lite.cdx.json
python -m epms.supply_chain verify-zip "<Portable ZIP>" portable sbom/epms-portable.cdx.json
```

각 ZIP 옆의 `.zip.sha256`과 ZIP 내부 SBOM을 대조한다. SBOM이 기록한 전체 DLL·EXE·PYD·JS 경로 집합과 각 파일 해시, ZIP 무결성, 고지 파일과 WebView2 포함 여부를 검증한다. 이전 ZIP과 SBOM에 현재의 의존 그래프를 소급해 넣지 않는다.

## 취약점 조회

```powershell
python -m epms.supply_chain scan-osv sbom/epms-development.cdx.json
python -m epms.supply_chain scan-osv sbom/epms-development.cdx.json --online
```

두 번째 명령은 네트워크 사용이 승인된 환경에서만 실행한다. 전송 데이터는 PyPI·npm 패키지명과 버전이다. 자료 파일, ZIP, 경로 이름, OCR 텍스트는 전송 데이터에 포함되지 않는다. 상태 `not_scanned`, `failed`, `partial`은 검사가 완료되지 않았음을 뜻한다. 운영체제와 PDFium·WebView2 같은 주요 바이너리는 공급자 공지와 실제 버전을 별도로 대조한다. [OSV API](https://google.github.io/osv.dev/api/)
