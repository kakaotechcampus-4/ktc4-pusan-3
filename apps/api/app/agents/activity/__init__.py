"""Activity Agent.
아이의 현재 관심과 실제 놀이 반응을 바탕으로, 지금 실행하기 좋은 놀이·외출 활동을 추천한다.

하는 일은 놀이 추천 하나(라벨 없음). 실내/야외는 날씨·시간 조회 결과로 갈리는 런타임 판단이다.
아이의 월령과 위치·날씨에 따라 쓸 수 있는 tool이 달라지고, 그 조합은 코드(registry)가 정한다.

tool 구현부는 주석, run()은 LLM·DB를 부르지 않는 mock
하지 않는 일 : 한 번 즐긴 놀이를 장기 취향으로 확정, 외부 리뷰만 보고 장소 단정,
             예약·결제(경로 자체가 없다), observation_education · observation_routine 읽기

설계: docs/agents/activity/activity-agent-v1.md
"""
