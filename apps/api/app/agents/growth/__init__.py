"""Growth Agent.
아이의 기록(교육 · 루틴 · 놀이)을 문서 행 · 도서와 이어 "다음에 해볼 것"을 제안하고, 키 · 몸무게
기록은 판정 없이 수치 그대로 정리한다. 설계: docs/agents/growth/

라벨 넷 — learning_suggestion · routine_coaching · book_suggestion · growth_review.
`growth_review` 는 모델을 부르지 않는다. 나머지 셋도 모델 앞뒤의 규칙(게이트 · 닫힘 · 안전 필터 ·
루틴 근거)은 코드가 정한다. 모델을 부르는 경로(`run()` 루프 · 프롬프트)는 아직 없다.

하지 않는 일 : 아이 비교 · 발달 판정 · 능력 확정, 음식 · 재료가 들어가는 놀이(Activity 몫),
             의료 처치 · 증상 교정, 관찰 직접 쓰기(`observation_activity` 로는 가지 않는다)

Activity 패키지를 import 하지 않는다 (docs/agents/README.md §6) — 놀이 기록은 포트로 받는다.
"""
