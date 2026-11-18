# PHASE 8 공급망 보안 검증 기록

확인일 2026-11-17.

| 범위 | 결과 |
| --- | --- |
| 개발 SBOM | 구성요소 59개, 의존 관계 80개. Vite JavaScript 청크에서 npm 패키지 9개 관찰. 동일 입력으로 연속 생성한 SBOM의 SHA-256이 일치 |
| 기존 Lite ZIP | ZIP SHA-256, 내부 SBOM과 133개 파일 해시 대조 통과. WebView2 미동봉 |
| 기존 Portable ZIP | ZIP SHA-256, 내부 SBOM과 172개 파일 해시 대조 통과. WebView2 Fixed Version 157.0.4341.57 동봉 |
| 두 ZIP의 주요 런타임 | CPython 3.12.14, PDFium 153.0.7999.0. 현지 WebView2 파일의 Microsoft 서명 확인 |
| OSV 최신 조회 | PyPI·npm 패키지 57개가 조회 대상. 네트워크 연결 차단 `WinError 10013`으로 응답을 받지 못해 스캔 완료 0개 |
| npm 오프라인 감사 | 출력상 0건. 최신 데이터베이스 응답을 확인할 수 없어 최신 판정에 사용하지 않음 |

Python의 기존 설치값과 일치하도록 `tzdata` 2026.6 및 개발용 `httpx` 0.28.1을 고정했다. npm lockfile은 기존 버전을 사용했다. 새로 설치한 SCA 도구 패키지는 없으며 OSV 조회는 Python 표준 라이브러리를 사용한다.

## 확인한 개별 공급자 공지

| 식별자 또는 공급자 | 적용성 및 후속 조치 |
| --- | --- |
| Pillow PDF append 관련 GHSA-p4wg-p73j-c528, CVE-2026-96640 | 공급자 공지의 영향 범위는 Pillow 12.3.0 이하이고 수정 버전은 현재 사용값 13.0.0이다. EPMS는 PDFium으로 PDF를 렌더링하고 Pillow에는 이미지 처리 경로를 사용한다. 해당 두 공지에 한해 별도 수정 불필요로 기록했다. [첫 번째 공급자 공지](https://github.com/python-pillow/Pillow/security/advisories/GHSA-p4wg-p73j-c528) · [두 번째 공급자 공지](https://github.com/python-pillow/Pillow/security/advisories/GHSA-mrfp-p49p-3hf2) |
| pypdfium2 및 PDFium | 현재 조합은 pypdfium2 5.13.0과 PDFium 153.0.7999.0이다. 상위 5.15.0 릴리스는 변경된 PDFium 빌드와 일부 CVE 수정이 포함됨을 명시한다. 배포 전 영향 범위 및 호환성 확인이 필요하며, 해당 공지만으로 현재 DLL의 개별 CVE 적용 여부를 확정할 수 없다. [pypdfium2 변경 기록](https://github.com/pypdfium2-team/pypdfium2/releases/tag/5.15.0) |
| WebView2 고정 런타임 | Portable의 기록 버전을 확인했다. Microsoft Edge 및 WebView2 공개 업데이트 정보를 검토했으며, 오프라인 고정 패키지의 최신 보안 상태와 재배포 약관 검토는 남아 있다. [Microsoft Edge 릴리스 정보](https://learn.microsoft.com/en-us/deployedge/microsoft-edge-relnote-stable-channel) |

2026-11-18 v1.3.0 준비 중 개발 SBOM의 PyPI·npm 57개와 새 Lite·Portable SBOM 각각의 67개를 OSV로 일괄 조회했다. 세 응답에 취약점 ID가 없었다. OSV 결과는 PDFium DLL과 WebView2의 보안 조사 범위를 포함하지 않는다. 재현 명령은 [SBOM 가이드](SBOM_GUIDE.md)에 있다.

2026-11-18 v1.3.1에서도 개발 SBOM 57개, Lite와 Portable SBOM 각 67개를 OSV로 조회했다. PyPI·npm 세 범위 모두 조회를 완료했고 응답에 취약점 ID가 없었다. PDFium DLL과 WebView2는 이 조회에 포함되지 않는다.
