# 오픈소스 구성요소 관리

`python -m epms.oss_inventory . development`는 현재 빌드 입력의 구성요소와 배포 고지 후보를 생성한다. Python은 설치 패키지의 메타데이터와 원본 라이선스 파일을 읽으며, 배포 빌드에서는 PyInstaller 분석에 등장한 추가 패키지를 확인한다. JavaScript는 lockfile의 런타임 의존성을 기준으로 수집한다. 수집된 원문은 `licenses/`에서 변경 없이 제공하며, 프로그램 도움말에서도 로컬 페이지를 열 수 있다.

Python 런타임 고지와 pypdfium2 배포본의 PDFium·Windows x64 하위 라이선스 파일은 별도로 포함해 확인해야 한다. Windows OCR과 PowerShell은 OS 구성요소를 호출한다. Lite는 설치된 WebView2를 이용하고 Portable은 Microsoft 서명이 있는 Fixed Version Runtime을 동봉한다. Microsoft에서 제공하는 Fixed Version 약관 전문과 credits 파일을 포함하고, Portable 전용 창을 처음 시작할 때 약관 안내와 동의를 표시한다. [Microsoft WebView2 배포 문서](https://learn.microsoft.com/microsoft-edge/webview2/concepts/distribution) · [공식 약관 원문 API](https://developer.microsoft.com/microsoft-edge/api/eula/webview2?locale=en-us&fixed=true)

라이선스 메타데이터와 실제 원문을 대조한 뒤 SPDX 식별자와 고지·NOTICE·저작권을 확정한다. 허용적 라이선스의 원저작권과 면책문은 보존하고, copyleft 계열은 실제 결합·배포방식의 의무를 확인한다. 2026-11-18에 `proxy_tools 0.1.0`의 배포 소스와 원본 커밋의 소스를 대조하고 BSD-2-Clause 원문을 확보했다. [출처 기록](../licenses/upstream/python/proxy-tools/0.1.0/SOURCE.md)

## 빌드 및 SBOM

`package.ps1`은 Lite와 Portable 각각에 고지·원문·CycloneDX JSON을 포함하고 ZIP SHA-256을 별도 파일로 저장한다. `-OutputRoot`로 검증 빌드의 경로를 지정할 수 있다. 신규 생성기는 JavaScript 청크에서 확인한 패키지와 설치 메타데이터·lockfile의 의존 관계를 기록한다. 모든 바이너리의 소유 패키지 매핑과 재배포 조건은 후속 검토 항목이다. [SBOM 검증 절차](SBOM_GUIDE.md)

2026-11-18 v1.3.0 준비 중 OSV 온라인 조회를 다시 실행해 PyPI·npm 패키지 57개 응답을 받았다. 확인한 공급자 공지와 남은 바이너리 검토는 [공급망 보안 검증 기록](SECURITY_DEPENDENCY_REPORT.md)에 있다.
