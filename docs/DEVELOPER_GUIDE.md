# 개발자 안내

Python 패키지는 requirements 파일로, 화면 패키지는 npm 잠금 파일로 설치합니다. `npm run build`가 TypeScript 검사와 번들을 생성합니다. `python -m pytest -q -s`와 `npm test -- web/workflow.test.ts`로 자동화 테스트를 실행합니다.

브라우저 테스트는 별도 시험 DB로 `python launch.py --port 18766 --db 시험.db --no-browser`를 실행한 뒤 `npm run test:e2e`를 실행합니다. 기본 브라우저 채널은 Microsoft Edge입니다.

`.\package.ps1 -WebViewRuntime build/WebView2`는 Windows x64 전용 창을 빌드하고 런타임과 함께 ZIP을 생성합니다. Microsoft의 WebView2 다운로드 페이지에서 **Fixed Version / x64** CAB를 받아 `expand.exe <CAB> -F:* <폴더>`로 풉니다. `-WebViewRuntime`에 `msedgewebview2.exe`가 들어 있는 폴더를 전달합니다. 빌드는 Microsoft 서명을 확인합니다. 런타임 업데이트 시 새 CAB로 패키지를 재생성합니다.

주요 경로는 `/api/organizations`, `/api/org/{org}/data/{kind}`, `/import/{kind}`, `/policies`, `/runs`, `/submission`, `/audit`입니다. 조직 경로의 후속 엔드포인트는 동일 접두사를 사용합니다. UI는 `/api/session`에서 받은 토큰을 이후 API 요청 헤더에 넣습니다. 요청과 검증 모델은 `epms/api.py`를 참고합니다.
