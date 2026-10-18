"""Activity Agent.
아이의 현재 관심과 실제 놀이 반응을 바탕으로, 지금 실행하기 좋은 놀이·외출 활동을 추천한다.

하는 일은 놀이 추천 하나(라벨 없음). 실내/야외는 날씨·시간 조회 결과로 갈리는 런타임 판단이다.
아이의 월령과 위치·날씨에 따라 쓸 수 있는 tool이 달라지고, 그 조합은 코드(registry)가 정한다.

run()은 사전 조회 + tool calling 루프다. pipeline 에 등록돼 있고(#380), 러너와 05 가 health_safety
어댑터를 넘긴다(#460). 안 넘긴 호출은 동의와 상관없이 blocked.safety 로 닫힌다 (UnwiredSafety).
하지 않는 일 : 한 번 즐긴 놀이를 장기 취향으로 확정, 외부 리뷰만 보고 장소 단정,
             예약·결제(경로 자체가 없다), observation_education · observation_routine 읽기

설계: docs/agents/activity/activity-agent-v1.md
"""
