"""Supervisor. 발화 한 줄을 조각으로 나누고 어디로 보낼지 라벨을 붙인다.

밖으로 열린 것은 `run(raw_text)` 하나고,
조각을 MemoryTask · FoodTask 로 바꾸는 건 routing(규칙),
그 둘을 실제로 부르는 건 pipeline 이다.

Supervisor 가 하는 것:
- 조각 나누기 · kind(record/request/guarded/out_of_scope/unclear) ·
  작업 종류(observe/schedule/lookup_edit) · agent · food 유형(식단 추천/영양소 분석).
"""
