"""증상 반복 hook — 최근 7일 안에 같은 증상이 기록된 날이 3일 이상인지.

단위는 날이다. 같은 날 여러 번은 1일이고, 기간 기록은 그 날들을 다 센다. 에피소드로 세지 않는다.
"""

from datetime import datetime, timedelta

import pytest

from app.agents.health.hook import (
    REPEAT_MIN_DAYS,
    REPEAT_WINDOW_DAYS,
    check_symptom_repetition,
    symptom_days,
)
from app.agents.health.readouts import READOUTS, SYMPTOM_REPEAT
from app.agents.health.store.inmemory import InMemoryHealthMemory

from .support import KST, TODAY, context, observation

DAY = timedelta(days=1)


def _days_ago(*offsets: int, symptoms: tuple[str, ...] = ("기침",)):
    return [observation(TODAY - offset * DAY, symptoms=symptoms) for offset in offsets]


def _counts(records) -> dict[str, int]:
    days = symptom_days(records, today=TODAY, timezone=KST)
    return {code: len(found) for code, found in days.items()}


def test_설정값():
    assert (REPEAT_WINDOW_DAYS, REPEAT_MIN_DAYS) == (7, 3)


def test_같은_날_세_번은_1일():
    assert _counts(_days_ago(0, 0, 0)) == {"cough": 1}


async def test_같은_날_세_번이면_안내가_나가지_않는다():
    ctx = context(memory=InMemoryHealthMemory(_days_ago(0, 0, 0)))

    assert await check_symptom_repetition(ctx) == ()


async def test_사흘_내리면_3일이고_안내가_나간다():
    ctx = context(memory=InMemoryHealthMemory(_days_ago(2, 1, 0)))

    readouts = await check_symptom_repetition(ctx)

    assert len(readouts) == 1
    assert readouts[0].kind == "symptom_repeat"
    assert readouts[0].authored_by == "code"
    assert readouts[0].body == READOUTS.render(SYMPTOM_REPEAT, symptom="기침", n="3").body
    assert readouts[0].body == (
        "최근 1주 동안 '기침'이(가) 3번 기록됐어요. 병원에서 확인해 보시는 게 좋겠어요."
    )


def test_기간_기록은_그_날들을_다_센다():
    # "사흘째 기침해" — 기록 1건이 3일이다 (개정 4 의 핵심 사례)
    records = [observation(TODAY - 2 * DAY, to=TODAY)]

    assert _counts(records) == {"cough": 3}


def test_observed_time_이_없으면_관찰한_날로_센다():
    # "어제 기침했어" 를 오늘 말해도 어제다. Memory 가 어제로 푼 observed_range 를 쓴다
    days = symptom_days(_days_ago(1), today=TODAY, timezone=KST)

    assert days == {"cough": {TODAY - DAY}}


@pytest.mark.parametrize(
    ("utc_time", "offset"),
    [
        ("2026-09-27T14:30:00+00:00", 1),  # KST 9/27 23:30
        ("2026-09-27T15:30:00+00:00", 0),  # KST 9/28 00:30 — UTC 날짜로 읽으면 하루 밀린다
    ],
)
def test_UTC_시각도_아이_타임존_날짜로(utc_time, offset):
    record = observation(TODAY - offset * DAY, at=datetime.fromisoformat(utc_time))

    days = symptom_days([record], today=TODAY, timezone=KST)

    assert days == {"cough": {TODAY - offset * DAY}}


def test_7일_창_밖은_세지_않는다():
    # 1 · 4 · 8일 전 → 창 안은 2일
    assert _counts(_days_ago(1, 4, 8)) == {"cough": 2}


def test_창의_경계는_6일_전까지다():
    assert _counts(_days_ago(6)) == {"cough": 1}
    assert _counts(_days_ago(7)) == {}


def test_기간이_창에_걸치면_창_안_날만_센다():
    records = [observation(TODAY - 9 * DAY, to=TODAY - 5 * DAY)]

    assert _counts(records) == {"cough": 2}  # 6일 전 · 5일 전


def test_사전_밖_증상은_세지_않는다():
    # 원문이 같아도 코드가 없으면 묶지 않는다
    assert _counts(_days_ago(0, 1, 2, symptoms=("몸이 이상해",))) == {}


def test_한_기록의_증상이_둘이면_각자_센다():
    assert _counts(_days_ago(0, symptoms=("열나", "기침"))) == {"fever": 1, "cough": 1}


def test_한_기록에_같은_증상이_두_표현이면_1일():
    assert _counts(_days_ago(0, symptoms=("열나", "38도"))) == {"fever": 1}


async def test_동의가_없으면_읽지_않는다():
    memory = InMemoryHealthMemory(_days_ago(2, 1, 0))
    ctx = context(consent=False, memory=memory)

    assert await check_symptom_repetition(ctx) == ()
    assert memory.calls == 0


async def test_7일_창만_저장소에_요청한다():
    memory = InMemoryHealthMemory(_days_ago(2, 1, 0))

    await check_symptom_repetition(context(memory=memory))

    assert memory.requested == [(TODAY - 6 * DAY, TODAY)]


async def test_반복이_선_증상마다_안내가_나간다():
    records = _days_ago(2, 1, 0, symptoms=("열나", "기침")) + _days_ago(0, symptoms=("콧물",))
    ctx = context(memory=InMemoryHealthMemory(records))

    readouts = await check_symptom_repetition(ctx)

    assert [r.body.split("'")[1] for r in readouts] == ["발열", "기침"]
