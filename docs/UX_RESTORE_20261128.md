# v2.0.4 표시 방식 복원 검증

2026-11-28 사용자 요청에 따라 2.0.3에서 추가한 글자·버튼 확대와 목록 중심 매칭 전환을 취소했다. 기존 연결도 기본 화면, 클릭·드래그 연결, 확대·축소 및 화면 맞춤을 복원했다. 계약 검토 차단·입력 보호·입력 누락 안내·숫자 오류 복구 등 업무 보완은 유지했다.

- TypeScript/Vite 빌드 통과.
- 화면 로직 테스트 36개 통과.
- Edge 업무 시나리오 5개 통과. 연결도 기본 표시, 12px 기본 버튼, 축소·화면 맞춤, 정산 작업 유지와 계약·입력 보호를 확인했다.
- Lite EXE 내장 서버에서도 같은 시나리오 5개 통과.
- 배포 패키지는 별도 release/v204 폴더에 생성하며 이전 배포본과 운영 DB는 변경하지 않았다.

근거: output/ux-v204-build.log, output/ux-v204-tests.log, output/ux-v204-browser.log, output/ux-v204-packaged-browser.log, output/ux-v204-graph.png.
