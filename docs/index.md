# 아이캐치 개발 문서

> **육아를 가장 많이 아는 AI가 아니라, 우리 아이를 가장 오래 알아온 AI.**

아이캐치는 보호자가 평소 하는 아이 이야기로 Child Memory 를 만들고, 그 기억을 근거로 식사 · 놀이 · 교육 · 건강 네 영역에서 다음 행동을 준비하는 서비스다. 카카오테크 캠퍼스 4기 부산대 3팀이 만든다.

이 사이트는 저장소의 `docs/` 폴더를 그대로 보여 준다. 기능마다 무엇을 정했는지 · 왜 그렇게 정했는지 · 어떻게 검증했는지가 문서로 남아 있다.
문서 안의 코드 링크는 GitHub 저장소의 `develop` 브랜치로 이어진다. API 계약의 정본은 Swagger 다.

## 바로가기

| 무엇 | 주소 |
| --- | --- |
| 서비스 (웹) | <https://icatch.ai.kr> |
| Android 앱 (Google Play) | <https://play.google.com/store/apps/details?id=kr.ai.icatch> |
| API 문서 — Swagger | <https://api.icatch.ai.kr/docs> |
| API 문서 — ReDoc | <https://api.icatch.ai.kr/redoc> |
| GitHub 저장소 | <https://github.com/kakaotechcampus-4/ktc4-pusan-3> |

## 처음이면 이 순서로

1. [설계 결정](overview/decisions-v1.md) — 만들면서 원래 계획을 바꾼 결정과 그 계기 · 대가 · 근거
2. [에이전트 설계](agents/supervisor-agent-v1.md) — Supervisor 의 의도 분류 · 라우팅부터 Memory · Curator · 도메인 Agent 까지
3. [안전 설계](safety/hazard-rules-v1.md) — 위험 물질 규칙 · 외부 LLM 으로 나가는 아이 정보 · 보관과 삭제 기준
4. [AI 품질 평가](eval/eval-v1.md) — 능력 × 절대 규칙으로 짠 평가 설계와 프롬프트별 측정
5. [운영](ops/deploy-v1.md) — CI 게이트 · 자동 롤백 · dev/prod 분리 · 로그와 알림
