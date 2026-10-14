"""월령 · 단계 계산 — `reference/age_gates.yaml` 의 경계를 붙인 것 (#372).

`app/rules/age.py` 는 표준 라이브러리만 쓰는 층이라 YAML 을 읽을 수 없어서 단계 경계를
인자로 받는다.
Agent 는 거기 있는 함수를 직접 부르지 않고 이 모듈의 같은 이름 함수를 쓴다 — 경계가 파일 한 곳에서만
오게 하려는 것이다. 월령 계산(`months_between`)과 나이 문구(`age_display`)는 경계를 안 쓰므로
`rules/age.py` 것을 그대로 쓴다.
"""

from datetime import date

from app.agents.common.reference import age_gates
from app.rules import age as rules_age
from app.rules.age import LifeStage, Stage


def life_stage(birth_date: date, today: date) -> LifeStage:
    """생년월일과 기준일로 월령 · 단계를 계산한다. today 는 호출하는 쪽이 넘긴다."""
    return rules_age.life_stage(birth_date, today, age_gates().stage_boundaries)


def stage_of(months: int) -> Stage:
    """월령을 단계로 바꾼다. 경계 월령이 되는 날 다음 단계가 된다."""
    return rules_age.stage_of(months, age_gates().stage_boundaries)


def first_month_of(stage: Stage) -> int:
    """그 단계가 시작하는 월령. 월령을 모르고 단계만 알 때 가장 어린 쪽으로 볼 때 쓴다."""
    return rules_age.first_month_of(stage, age_gates().stage_boundaries)
