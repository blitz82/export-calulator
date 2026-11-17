# PHASE 10 Windows 패키징 검증

## 배포본을 만들지 않는 사전 점검

`package.ps1 -Preflight`는 Lite·Portable 패키징 입력을 읽고 검증한 뒤 빌드를 시작하기 전에 종료한다. Windows x64 Python, 고정된 Python 의존성, PyInstaller와 데스크톱 모듈, OCR 스크립트·템플릿·마이그레이션, 공식 빈 양식과 안내문을 확인한다. Portable은 고정 WebView2 실행파일의 x64 헤더와 Microsoft 서명, 해당 버전 매니페스트를 추가로 확인한다.

```powershell
./package.ps1 -Python "<Windows x64 Python 경로>" -Lite -Preflight
./package.ps1 -Python "<Windows x64 Python 경로>" -Preflight
```

실제 빌드를 나중에 수행하면 ZIP 내부에 도구와 런타임 버전, OCR 추출기·템플릿 버전, 마이그레이션, 입력 파일 해시를 담은 `build-info.json`을 생성한다. 빌드의 최종 명령은 ZIP SHA-256, SBOM 구조와 실제 파일, x64 실행파일, 안내문, OCR 리소스, 잠금 파일 지문과 공식 빈 양식을 검증한다. 신고필증 샘플, 정답 데이터 및 운영 DB는 검증용 ZIP에 포함하지 않는다.

## 이번 확인 범위

| 항목 | 결과 |
| --- | --- |
| 현 소스 입력 검사 | Lite·Portable 모두 통과. 출력 디렉터리가 만들어지지 않음을 확인 |
| 현재 준비 버전 | Python 3.12.14, PyInstaller 6.22.3, OCR 템플릿 2, DB 마이그레이션 0001~0003. Portable WebView2 157.0.4341.57의 x64 헤더 및 서명 확인 |
| 기존 Lite ZIP | x64, SHA-256, SBOM과 133개 파일, 실행·OCR 안내 자료 및 공식 빈 양식 일치 |
| 기존 Portable ZIP | x64, SHA-256, SBOM과 172개 파일, 안내 자료와 공식 빈 양식, WebView2 동봉 확인 |
| 기존 ZIP의 적용 범위 | 두 ZIP은 이전 개발 시점 산출물이며, DB 마이그레이션은 0002까지 포함. 현재 소스의 0003과 이후 변경을 반영한 새 ZIP은 생성하지 않음 |
| 기존 EXE 실제 실행 | 검증한 ZIP을 임시 폴더에 풀고 Lite·Portable 모두 `--no-browser`로 실행. 자식 프로세스의 PATH에서 Python·Node.js를 찾을 수 없는 상태로 로컬 API 시작, 합성 전자 PDF 추출·미리보기·라이선스 페이지를 확인 |
| 제한된 Windows OCR | 기존 EXE 두 개가 스캔 PDF 접수 시 Windows OCR을 사용할 수 없다는 안내를 반환. 실행 정책 설정을 변경하지 않음 |
| 기존 DB 마이그레이션 | 각 기존 EXE에서 만든 합성 시험 원장 DB를 현재 소스로 열어 0002에서 0003으로 변경. 원장 행 ID와 내용이 기존과 일치 |

현재 소스를 패키징한 EXE와 데스크톱 WebView2 창, Python·Node.js 미설치 또는 물리적으로 네트워크가 끊긴 별도 Windows PC, 한국어 OCR 언어팩 미설치 PC 및 실제 운영 DB는 이번 인수시험 범위에 포함되지 않았다. [공급망 보안 검증 기록](SECURITY_DEPENDENCY_REPORT.md)의 최신 SCA·라이선스 보류 사항과 [OCR 정확도 검증](OCR_ACCURACY_VALIDATION.md)의 스캔 제약도 남아 있다. 새 버전 번호 확정, 외부 공개, 운영 환경 교체는 수행하지 않았다.
