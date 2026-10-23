# AGENTS.md

이 레포에서 일하는 AI 에이전트(Claude Code, Codex, Gemini 등)가 공통으로 읽는 규칙. 사람이 직접 관리하고, 짧게 유지한다.

## 프로젝트
DART 감사보고서와 공개 자료로 비상장 로보틱스 스타트업 투자 검토 보고서를 쓰는 LangGraph Supervisor 멀티 에이전트.

## 실행과 테스트
- 설치: `pip install -r requirements.txt`
- 테스트: `python -m pytest -q tests` (LLM을 가짜 응답으로 바꿔 돌려서 API 키 없이 된다)
- 실행: `python main.py "회사명 투자 가능성 평가해줘"` (`.env`에 OPENAI_API_KEY, OPENDART_API_KEY 필요)
- 코드를 바꾸면 테스트를 돌리고, 통과한 상태로만 PR을 올린다.

## 지켜야 할 설계 원칙
- 숫자는 코드가 계산한다. 재무 계정 추출, 성장률, 현금 소진 기간, 스코어카드 가중합을 LLM에게 넘기지 않는다.
- LLM은 판단(근거 충분성, 1~5점, 서술)만 한다. 분기와 허용 후보 계산은 `supervisor.py`, `graph.py` 코드가 한다.
- 실행 상한(MAX_STEPS 등)은 `invest_agent/config.py` 상수로만 바꾼다. 프롬프트에 상한을 적어서 해결하지 않는다.
- 스코어카드 표, 재무 원본 표, REFERENCE는 `agents/writer.finalize_markdown()`이 넣는다. 작성 모델이 표를 만들게 바꾸지 않는다.
- 하위 에이전트끼리 직접 연결하지 않는다. 모든 통신은 Supervisor를 거친다.

## 하지 말 것
- `.env`나 API 키를 커밋하지 않는다. 예시는 `.env.example`에만 쓴다.
- `examples/`의 보고서 결과물은 실제 실행 결과다. 손으로 고치지 않는다.
- 관련 없는 파일을 정리하거나 지우지 않는다. 필요해 보이면 PR 설명에 제안만 적는다.

## 커밋과 PR
- 커밋 메시지는 한국어, `종류: 무엇을 왜` 형태 (예: `fix: 현금 소진 기간이 음수일 때 0으로 처리`).
- PR 하나에 한 가지 변경만 담는다.
