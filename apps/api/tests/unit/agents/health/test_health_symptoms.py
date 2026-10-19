"""증상 정규화 · 에피소드 묶기.

반복 판정(hook)은 기록된 날 수로 세고, 에피소드는 진료 준비 타임라인 표시용이다. 두 쪽이 같은
증상 어휘와 같은 날짜 규칙 위에 서야 해서 날짜 펼치기도 여기 있다.
"""

from datetime import datetime, timedelta

import pytest

from app.agents.health.tools.symptoms import (
    EPISODE_GAP,
    group_symptom_episodes,
    normalize_symptom_term,
    observation_days,
    symptom_codes_of,
)

from .support import KST, TODAY, observation

DAY = timedelta(days=1)


@pytest.mark.parametrize("text", ["열나", "열나요", "미열", "38도", "열이 나요", "고열"])
def test_열나_미열_38도는_fever(text):
    assert normalize_symptom_term(text) == "fever"


def test_사전_밖은_None():
    # 원문은 호출부가 그대로 둔다 — 코드로 바꾸지 않는다
    assert normalize_symptom_term("컨디션이 별로") is None
    assert normalize_symptom_term("") is None


def test_콧물이_났는데_열은_없었대_에서_열을_뽑지_않는다():
    assert normalize_symptom_term("콧물이 났는데 열은 없었대") == "runny_nose"
    assert normalize_symptom_term("열이 없어요") is None


def test_한_문장에_증상이_둘이면_하나로_고르지_않는다():
    # 하나만 골라 돌려주면 나머지 증상의 날이 조용히 사라진다
    assert normalize_symptom_term("열나고 기침해요") is None
    assert symptom_codes_of(["열나고 기침해요"]) == ("fever", "cough")


def test_코드_목록은_순서를_지키고_중복을_뺀다():
    assert symptom_codes_of(["열나", "기침", "38도", "이상한 말"]) == ("fever", "cough")


def test_기간_기록은_그_날들을_다_펼친다():
    record = observation(TODAY - 2 * DAY, to=TODAY)

    assert observation_days(record, KST) == (TODAY - 2 * DAY, TODAY - DAY, TODAY)


def test_observed_time_이_있으면_아이_타임존의_그_날():
    # 15:30 UTC = KST 다음 날 00:30. UTC 날짜로 읽으면 하루 어긋난다
    at = datetime.fromisoformat("2026-09-27T15:30:00+00:00")
    record = observation(TODAY - DAY, at=at)

    assert observation_days(record, KST) == (TODAY,)


def test_사흘_내리_발열은_에피소드_1건():
    records = [observation(TODAY - offset * DAY, symptoms=("열나",)) for offset in (2, 1, 0)]

    episodes = group_symptom_episodes(records, timezone=KST)

    assert len(episodes) == 1
    episode = episodes[0]
    assert (episode.code, episode.days) == ("fever", 3)
    assert (episode.starts_on, episode.ends_on) == (TODAY - 2 * DAY, TODAY)
    assert set(episode.observation_ids) == {record.id for record in records}


def test_48시간_넘게_떨어지면_2건():
    records = [
        observation(TODAY - 3 * DAY, symptoms=("열나",)),
        observation(TODAY, symptoms=("열나",)),
    ]

    assert len(group_symptom_episodes(records, timezone=KST)) == 2


def test_48시간_안이면_하루_건너뛰어도_1건():
    records = [
        observation(TODAY - 2 * DAY, symptoms=("열나",)),
        observation(TODAY, symptoms=("열나",)),
    ]

    assert len(group_symptom_episodes(records, timezone=KST)) == 1


def test_증상이_다르면_에피소드도_따로():
    records = [observation(TODAY, symptoms=("열나", "기침"))]

    codes = sorted(episode.code for episode in group_symptom_episodes(records, timezone=KST))

    assert codes == ["cough", "fever"]


def test_사전_밖_증상은_에피소드로_묶지_않는다():
    records = [
        observation(TODAY - DAY, symptoms=("몸이 이상해",)),
        observation(TODAY, symptoms=("몸이 이상해",)),
    ]

    assert group_symptom_episodes(records, timezone=KST) == ()


def test_간격은_설정값이다():
    assert EPISODE_GAP == timedelta(hours=48)
