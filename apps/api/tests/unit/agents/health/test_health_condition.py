"""반복 안내에 등록된 만성질환 잇기 — (질환 × 증상) 행이 있을 때만.

연결은 이름을 맞추는 일이다. 증상에서 질환을 찾지 않고, 등록 질환이 있다는 것만으로 아무 증상에나
질환 문장을 붙이지 않는다. 알레르기는 붙이지 않고, 조회에 실패하면 "등록 질환 없음" 이라 말하지 않고
기본 문장만 낸다. 아무것도 쓰지 않는다.
"""

from datetime import timedelta

from app.agents.health.hook import (
    ConditionMatch,
    check_symptom_repetition,
    lookup_condition_guidance,
)
from app.agents.health.reference import Guidance
from app.agents.health.store.inmemory import InMemoryHealthMemory, InMemoryHealthSafety
from app.agents.health.store.ports import HealthSafetyEntry

from .support import TODAY, context, observation

DAY = timedelta(days=1)

# 지어낸 행이다 — 운영 yaml 의 검수 상태와 상관없이 연결 동작만 본다
IMMUNE_COUGH = Guidance(
    condition="immunocompromised",
    symptom="cough",
    text="테스트 안내 문구",
    source_title="테스트 문서",
    publisher="테스트 기관",
    url="https://example.org/guide",
    license="KOGL-1",
)
ASTHMA_COUGH = Guidance(
    condition="asthma",
    symptom="cough",
    text="천식 테스트 안내 문구",
    source_title="천식 테스트 문서",
    publisher="테스트 기관",
    url="https://example.org/asthma",
    license="KOGL-1",
)
GUIDANCE = {
    ("immunocompromised", "cough"): IMMUNE_COUGH,
    ("asthma", "cough"): ASTHMA_COUGH,
}

IMMUNE = HealthSafetyEntry("chronic_disease", "만성 면역력 저하증", "active")
ASTHMA = HealthSafetyEntry("chronic_disease", "소아천식", "active")
PEANUT = HealthSafetyEntry("allergy", "땅콩", "active")

DEFAULT_COUGH = "최근 1주 동안 '기침'이(가) 3번 기록됐어요. 병원에서 확인해 보시는 게 좋겠어요."


def _cough_three_days(symptoms: tuple[str, ...] = ("기침",)) -> InMemoryHealthMemory:
    return InMemoryHealthMemory(
        [observation(TODAY - n * DAY, symptoms=symptoms) for n in (2, 1, 0)]
    )


async def _readouts(entries, *, memory=None, guidance=GUIDANCE, fail=False):
    safety = InMemoryHealthSafety(entries, fail=fail)
    ctx = context(safety=safety, memory=memory or _cough_three_days())
    return await check_symptom_repetition(ctx, guidance=guidance), safety


async def test_등록_질환과_행이_맞으면_질환_문장과_카드():
    readouts, _ = await _readouts([IMMUNE], guidance={("immunocompromised", "cough"): IMMUNE_COUGH})

    assert [r.kind for r in readouts] == ["symptom_repeat", "condition_guidance"]
    # 명세 §4 문구와 글자 단위로 같고, {condition} 은 보호자가 적은 이름 원문이다
    assert readouts[0].body == (
        "최근 1주 동안 '기침'이(가) 3번 기록됐어요. "
        "등록하신 '만성 면역력 저하증'이(가) 있어 병원에서 확인해 보시는 게 좋겠어요."
    )
    assert readouts[1].title == "출처: 테스트 기관 「테스트 문서」"
    assert readouts[1].body == "테스트 안내 문구"
    assert all(r.authored_by == "code" for r in readouts)


async def test_행이_없으면_기본_문장():
    # 면역저하는 등록돼 있지만 (면역저하 × 발열) 행은 없다
    memory = InMemoryHealthMemory(
        [observation(TODAY - n * DAY, symptoms=("열나",)) for n in (2, 1, 0)]
    )

    readouts, _ = await _readouts([IMMUNE], memory=memory)

    assert [r.body for r in readouts] == [
        "최근 1주 동안 '발열'이(가) 3번 기록됐어요. 병원에서 확인해 보시는 게 좋겠어요."
    ]


async def test_안내_행이_하나도_없으면_건강정보를_읽지도_않는다():
    readouts, safety = await _readouts([IMMUNE], guidance={})

    assert [r.body for r in readouts] == [DEFAULT_COUGH]
    assert safety.calls == 0


async def test_반복이_서지_않으면_건강정보를_읽지_않는다():
    memory = InMemoryHealthMemory([observation(TODAY, symptoms=("기침",))])

    readouts, safety = await _readouts([IMMUNE], memory=memory)

    assert readouts == ()
    assert safety.calls == 0


async def test_사전에_없는_질환_이름이면_기본_문장():
    rare = HealthSafetyEntry("chronic_disease", "테스트 희귀질환", "active")

    readouts, _ = await _readouts([rare])

    assert [r.body for r in readouts] == [DEFAULT_COUGH]


async def test_알레르기는_붙이지_않는다():
    # 이름이 질환 사전에 걸려도 kind 가 allergy 면 연결하지 않는다
    sneaky = HealthSafetyEntry("allergy", "천식", "active")

    readouts, _ = await _readouts([PEANUT, sneaky])

    assert [r.body for r in readouts] == [DEFAULT_COUGH]


async def test_other_medical_도_잇는다():
    other = HealthSafetyEntry("other_medical", "면역저하", "active")

    readouts, _ = await _readouts([other])

    assert [r.kind for r in readouts] == ["symptom_repeat", "condition_guidance"]
    assert "'면역저하'" in readouts[0].body


async def test_철회한_항목은_잇지_않는다():
    retracted = HealthSafetyEntry("chronic_disease", "만성 면역력 저하증", "retracted")

    readouts, _ = await _readouts([retracted])

    assert [r.body for r in readouts] == [DEFAULT_COUGH]


async def test_조회가_실패하면_기본_문장():
    readouts, _ = await _readouts([IMMUNE], fail=True)

    assert [r.body for r in readouts] == [DEFAULT_COUGH]
    assert "없" not in readouts[0].body  # "등록 질환 없음" 이라고 말하지 않는다


async def test_질환이_둘이면_둘_다_적는다():
    readouts, _ = await _readouts([IMMUNE, ASTHMA])

    assert readouts[0].body == (
        "최근 1주 동안 '기침'이(가) 3번 기록됐어요. "
        "등록하신 '만성 면역력 저하증' · '소아천식'이(가) 있어 병원에서 확인해 보시는 게 좋겠어요."
    )
    assert [r.body for r in readouts[1:]] == ["테스트 안내 문구", "천식 테스트 안내 문구"]


async def test_원인_병명_추정_표현이_없다():
    readouts, _ = await _readouts([IMMUNE, ASTHMA])

    for readout in readouts:
        for phrase in ("로 인해", "에 걸린", "때문"):
            assert phrase not in readout.title + readout.body


async def test_아무것도_쓰지_않는다():
    memory = _cough_three_days()
    safety = InMemoryHealthSafety([IMMUNE])
    ctx = context(safety=safety, memory=memory)
    before = (list(memory._observations), list(safety._entries))

    await check_symptom_repetition(ctx, guidance=GUIDANCE)

    assert (memory._observations, safety._entries) == before
    writers = ("save", "write", "create", "update", "delete", "set_", "add", "put")
    for port in (ctx.ports.memory, ctx.ports.safety):
        assert not [name for name in dir(port) if name.startswith(writers)]


def test_조회_결과가_없으면_None():
    assert lookup_condition_guidance("cough", [], GUIDANCE) is None
    assert lookup_condition_guidance("fever", [IMMUNE], GUIDANCE) is None


def test_같은_질환을_두_이름으로_적어도_안내는_한_번():
    again = HealthSafetyEntry("chronic_disease", "면역력 저하", "active")

    match = lookup_condition_guidance("cough", [IMMUNE, again], GUIDANCE)

    assert isinstance(match, ConditionMatch)
    assert match.labels == ("만성 면역력 저하증", "면역력 저하")
    assert match.guidances == (IMMUNE_COUGH,)


async def test_운영_안내_파일로_천식_기침_카드가_나간다():
    # guidance 를 넘기지 않으면 reference/condition_guidance.yaml 의 검수된 행을 쓴다
    safety = InMemoryHealthSafety([ASTHMA])
    ctx = context(safety=safety, memory=_cough_three_days())

    readouts = await check_symptom_repetition(ctx)

    assert [r.kind for r in readouts] == ["symptom_repeat", "condition_guidance"]
    assert "'소아천식'" in readouts[0].body
    assert (
        readouts[1].title
        == "출처: 건강보험심사평가원 「차가운 바람 부는 12월, 호흡기 약한 어린이 '천식' 주의」"
    )
    assert safety.calls == 1


async def test_운영_안내_파일에_없는_조합은_기본_문장():
    # 천식 × 발열 행은 없다
    memory = InMemoryHealthMemory(
        [observation(TODAY - n * DAY, symptoms=("열나",)) for n in (2, 1, 0)]
    )
    ctx = context(safety=InMemoryHealthSafety([ASTHMA]), memory=memory)

    readouts = await check_symptom_repetition(ctx)

    assert [r.kind for r in readouts] == ["symptom_repeat"]
    assert "등록하신" not in readouts[0].body
