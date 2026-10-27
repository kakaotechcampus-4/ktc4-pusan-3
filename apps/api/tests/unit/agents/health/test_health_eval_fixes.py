"""전달 서류 라우팅 · 응급 hook · 읽기 전용 출구 변경분의 eval 케이스를 모델 없이 돌려 본다.

`make test` 에서 돈다. 라이브 eval(`tests/eval/agents/health/test_health_run.py -m live`)은 같은
케이스를 실제 모델로 돌린다. 여기서는 두 가지를 본다.
  - 모델 0회 케이스는 가짜 모델 없이 판정을 통과한다 (전달 서류 · 영아 발열 hook · 체크 현황)
  - 판정이 어긋난 결과를 실패로 잡는다 — 서식이 바뀐 것 · 줄바꿈이 샌 것 · 코스가 합쳐진 것 · 응급
    안내가 빠진 것 · 쓰기 요청이 출구에 가려진 것 · 정리가 열 타임라인으로 샌 것
"""

from dataclasses import replace

import pytest

from app.agents.health.labels.medication import ACTION_WORDS
from app.agents.health.labels.visit import is_fever_question
from tests.eval.agents.health.cases import CASES
from tests.eval.agents.health.judges import common
from tests.eval.agents.health.runner import run_case
from tests.unit.agents.health.support import FakeLLM

BY_ID = {case.case_id: case for case in CASES}
MODEL_FREE = [
    "T10-2same",
    "T11-more",
    "T18-allergy",
    "T18-daycare",
    "T18-notelines",
    "T19-allergy-med",
    "T19-sitter",
    "T19-detail",
    "T25-infant",
    "T25-toddler",
    "T25-infant-noconsent",
]


async def _ran(case_id: str):
    return await run_case(BY_ID[case_id], client=FakeLLM())


def _failures(case_id: str, ran) -> list[str]:
    return [*common(ran).failures, *BY_ID[case_id].judge(ran).failures]


def _swapped(ran, old: str, new: str):
    """카드 · 서식 본문의 `old` 를 `new` 로 바꾼 결과. 바꿀 문구가 실제로 있어야 한다."""
    [readout] = ran.result.readouts
    assert old in readout.body, old
    changed = replace(readout, body=readout.body.replace(old, new))
    return replace(ran, result=replace(ran.result, readouts=(changed,)))


# ── 케이스 표 ──
def test_새_케이스는_전부_판정이_있다():
    for case_id in [*MODEL_FREE, "T10-mixed", "T22-mix"]:
        assert "pending" not in BY_ID[case_id].judge.__qualname__, case_id


def test_영아_발열_hook_케이스는_hook_을_고른다():
    infant = [c for c in CASES if c.hook == "infant_fever"]

    assert [c.case_id for c in infant] == ["T25-infant", "T25-toddler", "T25-infant-noconsent"]
    assert all(c.label is None and not c.memory for c in infant)
    for case in infant:
        assert case.judge.__qualname__.startswith("infant_fever_hook."), case.case_id


def test_새_케이스는_문서_입력_id_를_쓰고_변형_입력을_둔다():
    # 문서에 없는 변형은 text 에 둔다 — 문서의 [Txx] 목록은 그대로다
    for case_id in ("T18-allergy", "T19-sitter", "T25-infant", "T10-mixed", "T22-mix"):
        assert BY_ID[case_id].text, case_id


# ── 케이스가 판정을 통과한다(모델 0회) ──
@pytest.mark.parametrize("case_id", MODEL_FREE)
async def test_모델_없는_케이스가_판정을_통과한다(case_id):
    ran = await _ran(case_id)

    assert _failures(case_id, ran) == []
    assert ran.result.model_calls == 0


# ── 판정이 어긋난 결과를 잡는다 ──
async def test_알레르기를_말했는데_투약의뢰서가_나가면_카드_케이스가_실패한다():
    card = await _ran("T19-allergy-med")
    notice = await _ran("T18-allergy")
    wrong = replace(card, result=notice.result)

    assert _failures("T19-allergy-med", wrong)


async def test_서식_이름이_있는데_카드가_나가면_투약의뢰서_케이스가_실패한다():
    card = await _ran("T19-allergy-med")
    notice = await _ran("T18-allergy")
    wrong = replace(notice, result=card.result)

    assert _failures("T18-allergy", wrong)


async def test_특이사항_줄바꿈이_서식을_깨면_실패한다():
    ran = await _ran("T18-notelines")

    wrong = _swapped(ran, "흔들어서 냉장 보관", "흔들어서\n냉장 보관")

    assert _failures("T18-notelines", wrong)


async def test_카드에_반응_대처가_빠지면_실패한다():
    ran = await _ran("T19-detail")

    wrong = _swapped(ran, " / 대처: 에피펜 놓고 119", "")

    assert _failures("T19-detail", wrong)


async def test_카드의_대처_줄바꿈이_새면_실패한다():
    ran = await _ran("T19-detail")

    wrong = _swapped(ran, "두 번 증상이", "두 번\n증상이")

    assert _failures("T19-detail", wrong)


async def test_같은_이름_코스_둘이_한_장으로_합쳐지면_실패한다():
    ran = await _ran("T10-2same")
    merged = replace(ran.result, readouts=ran.result.readouts[:1])

    assert _failures("T10-2same", replace(ran, result=merged))


async def test_코스_ref_가_같으면_실패한다():
    ran = await _ran("T10-2same")
    first = ran.result.readouts[0]
    same_ref = replace(ran.result, readouts=(first, first))

    assert _failures("T10-2same", replace(ran, result=same_ref))


async def test_영아_발열인데_응급_안내가_없으면_실패한다():
    ran = await _ran("T25-infant")
    silent = replace(ran.result, readouts=())

    assert _failures("T25-infant", replace(ran, result=silent))


async def test_영아_발열이_아닌데_응급_안내가_나가면_실패한다():
    toddler = await _ran("T25-toddler")
    infant = await _ran("T25-infant")

    assert _failures("T25-toddler", replace(toddler, result=infant.result))


async def test_읽으면_안_되는_세계에서_건강_기록을_읽으면_실패한다():
    ran = await _ran("T25-infant-noconsent")
    await ran.world.context.ports.memory.health_observations(
        child_id=ran.world.context.child_id,
        date_from=ran.world.today,
        date_to=ran.world.today,
    )

    assert any("읽었다" in failure for failure in _failures("T25-infant-noconsent", ran))


async def test_쓰기_요청이_출구에_가려져_초안이_없으면_실패한다():
    # 출구만 통과해 loop 가 끝난 모양 — 등록 요청이 사라졌다
    status = await _ran("T10")
    mixed = BY_ID["T10-mixed"]
    wrong = replace(status, case_id="T10-mixed")

    verdict = mixed.judge(wrong)

    assert verdict.failures and any("초안" in failure for failure in verdict.failures)


async def test_정리_요청이_열_타임라인으로_새면_실패한다():
    fever = await run_case(BY_ID["T22"], client=FakeLLM())

    verdict = BY_ID["T22-mix"].judge(fever)

    assert verdict.failures


def test_변형_입력은_표에_안_걸리고_모델로_간다():
    # T10-mixed 는 등록 말("새로")이 있어 표가 가로채지 않는다 · T22-mix 는 열 표현 표에 안 걸린다
    assert any(word in BY_ID["T10-mixed"].text for word in ACTION_WORDS)
    assert not is_fever_question([BY_ID["T22-mix"].text])
