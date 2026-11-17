# OCR 의존성 선택

| 방식 | 이번 개발의 결정 |
| --- | --- |
| A. invoice2data 직접 의존 | 우선 검토했다. 고정 버전 Windows wheel을 확보하지 못해 실제 API, PDFium 호환성, 의존성 취약점 검증이 남아 있다. |
| B. 상위 프로젝트 코드 일부 반입 | 필요 기능을 EPMS 코드에 구현할 수 있어 반입하지 않았다. |
| C. EPMS 독립 구현 | 개발에 적용했다. 기존 PDFium 좌표, 신고필증 템플릿 및 승인 흐름을 활용하고 품목란의 부분 추출을 보강했다. |

invoice2data v1.0.1의 기본 의존성은 Click, python-dateutil, PyYAML, regex다. 태그는 PDFium 리더에 pypdfium2 4 이상을 명시하며, EPMS의 런타임 핀은 5.13.0이다. 이 조건은 선언상 호환 범위이며 Windows에서 실행해 확인한 결과는 아니다. [고정 버전 의존성 선언](https://github.com/invoice-x/invoice2data/blob/v1.0.1/pyproject.toml)

이번 작업의 런타임 신규 의존성과 반입 외부 코드는 없다. invoice2data는 기술 검토 대상이며 EPMS의 실제 구성요소와 배포 고지에 포함되지 않는다. 실행 실증을 재개할 경우 공식 Windows wheel과 전이 의존성의 버전·해시·라이선스 원문을 대조하고, 승인된 취약점 데이터로 확인한 뒤 개발 환경에서 시험한다.
