# 아키텍처

React·TypeScript 화면은 HashRouter와 TanStack Query를 사용합니다. 원장 검색·필터·정렬은 AG Grid Community가 담당합니다. Vite가 번들한 화면을 FastAPI가 같은 주소에서 제공합니다. API는 프로세스별 임의 토큰을 확인하며 실행기는 127.0.0.1에서만 수신합니다.

입력 경로는 API에서 정규화와 서버 검증을 거칩니다. SQLAlchemy 트랜잭션이 업무 데이터와 감사기록을 함께 저장합니다. SQLite WAL과 외래키를 사용합니다. Alembic 초기 개정으로 스키마를 관리합니다.

`rules.py`가 DB 및 화면과 독립적으로 검증과 Decimal 계산을 수행합니다. `books.py`가 XLSX 가져오기와 일반 보고서를 담당하고 `submission.py`가 공식 양식을 작성합니다. 계산 실행은 적용 정책, 원장, 명부, 검증과 귀속 결과를 스냅샷으로 보존합니다.

중앙 서버나 네트워크 공유 DB를 운영하는 기능은 후속 범위입니다. PostgreSQL 전환 시 SQLite 전용 업서트, 백업, 연결 설정을 변경해야 합니다.
