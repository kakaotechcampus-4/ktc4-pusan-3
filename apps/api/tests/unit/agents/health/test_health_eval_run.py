"""복약 · 전달 서류 eval 케이스를 모델 없이 돌려 판정이 맞는지 본다. `make test` 에서 돈다.

라이브 eval(`tests/eval/agents/health/test_health_run.py -m live`)은 실제 모델이 필요하다.
여기서는 같은 실행기(`runner.run_case`)로 케이스를 돌리되 모델 자리에 **정해 둔 tool 호출을
돌려주는 가짜**를 넣는다 — 판정이 맞는 결과를 통과시키는지, 어긋난 결과를 실패로 잡는지를
라이브 키 없이 본다.

    - 가짜 모델이 하는 일은 케이스마다 한 번의 tool 호출뿐이다. 이어받는 턴 · 모델 0회 경로는 코드다
    - 판정을 망가뜨린 기록(쓰기 · 시각 · 문구)이 실패로 잡히는지도 본다
"""

from dataclasses import replace

import pytest

from app.agents.common.readout import Readout
from app.agents.common.refs import Ref
from app.agents.health.readouts import READOUTS
from app.agents.health.schemas.task import HealthTaskType
from tests.eval.agents.health.cases import CASES
from tests.eval.agents.health.judges import (
    Turn,
    check_on_screen,
    common,
    dose_status,
    emergency_card,
    handoff_notice,
    med_stop,
)
from tests.eval.agents.health.runner import run_case
from tests.unit.agents.health.support import FakeLLM, call, calls

LABELS = {HealthTaskType.MEDICATION, HealthTaskType.CARE_HANDOFF}
BY_ID = {case.case_id: case for case in CASES}

# 모델이 한 번 부르는 tool. 원문에서 그대로 복사한 span 만 넘긴다
SCRIPTS = {
    "T01": call(
        "create_medication_schedule",
        {"title_span": "항생제", "dosage_span": "5ml", "timing_spans": ["하루 세 번 식후"]},
    ),
    "T02": call("create_medication_schedule", {"timing_spans": ["하루 세 번"]}),
    "T02-unclear": call("create_medication_schedule", {"timing_spans": ["하루 세 번"]}),
    "T03": call(
        "create_medication_schedule", {"title_span": "시럽약", "timing_spans": ["오후 3시"]}
    ),
    "T04": call(
        "create_medication_schedule",
        {"title_span": "유산균", "timing_spans": ["아침 먹기 30분 전"]},
    ),
    "T05": call(
        "create_medication_schedule",
        {
            "title_span": "항생제",
            "timing_spans": ["하루 세 번 식후"],
            "period_span": "5일",
            "indication_span": "중이염",
        },
    ),
    "T12": call(
        "update_medication_schedule",
        {"title_span": "항생제", "timing_spans": ["저녁 시간 7시"]},
    ),
    "T13": call("stop_medication_schedule", {"title_span": "항생제"}),
    "T13-no": call("stop_medication_schedule", {"title_span": "항생제"}),
    "T14": call("stop_medication_schedule", {}),
    "T14-2med": call("stop_medication_schedule", {}),
    "T14-2med-unclear": call("stop_medication_schedule", {}),
    "T15": call("stop_medication_schedule", {}),
    # 바꿀 값을 말하지 않고 용량만 물었다 — 모델은 처방한 곳에 확인하라는 출구를 고른다
    "T16": call("defer_dose_question", {}),
    # 표(QUERY_WORDS)에 안 걸린 현황 질문 — 모델이 읽기 전용 현황 출구를 고른다
    "T10-count": call("show_dose_status", {}),
    # 현황 질문과 새 약 등록이 섞인 말 — 모델이 한 응답에 출구와 등록을 같이 부른다. 출구는 거절되고
    # 등록 초안만 나가야 한다(쓰기 요청이 출구에 가려지지 않는다)
    "T10-mixed": calls(
        ("show_dose_status", {}),
        (
            "create_medication_schedule",
            {"title_span": "해열제", "timing_spans": ["하루 세 번 식후"], "period_span": "5일"},
        ),
    ),
}

JUDGED = [
    case for case in CASES if case.label in LABELS and not case.case_id.endswith("-noconsent")
]


def llm_for(case_id: str) -> FakeLLM:
    return FakeLLM(SCRIPTS[case_id]) if case_id in SCRIPTS else FakeLLM()


@pytest.mark.parametrize("case", JUDGED, ids=[case.case_id for case in JUDGED])
async def test_케이스가_판정을_통과한다(case):
    ran = await run_case(case, client=llm_for(case.case_id))

    verdict = common(ran).extend(case.judge(ran))

    assert verdict.failures == [], verdict.failures
    assert verdict.gaps == []


def test_복약_전달_케이스는_판정이_있다():
    # pending 판정이 남아 있으면 구현이 있는데 판정이 없다는 뜻이다
    for case in JUDGED:
        assert "pending" not in case.judge.__qualname__, case.case_id
    ids = {case.case_id for case in JUDGED}
    assert {"T01", "T02", "T03", "T04", "T05", "T07", "T08", "T09", "T10", "T11", "T12"} <= ids
    assert {"T13", "T13-no", "T14", "T14-2med", "T14-2med-unclear", "T15", "T16", "T21"} <= ids
    assert {"T18", "T18-nomed", "T19", "T19-safetyfail", "T02-unclear", "T07-nomed"} <= ids


def test_되묻기를_이어받는_케이스는_다음_턴_입력이_있다():
    assert {case_id: BY_ID[case_id].followups for case_id in BY_ID if BY_ID[case_id].followups} == {
        "T02": ("식후요",),
        "T02-unclear": ("몰라",),
        "T13": ("응",),
        "T13-no": ("아니 그냥 둬",),
        "T14-2med": ("항생제", "응"),
        "T14-2med-unclear": ("글쎄",),
        "T15": ("응",),
    }


async def test_중단은_답한_턴의_now_로_남는다():
    ran = await run_case(BY_ID["T13"], client=llm_for("T13"))

    first, second = ran.turns
    assert second.now > first.now  # 2분 뒤다
    assert first.writes == ()
    [(schedule_id, stopped_at)] = second.writes
    assert stopped_at == second.now
    assert ran.world.medication.stopped == {schedule_id: second.now}
    assert [c.title for c in ran.world.courses if c.id == schedule_id] == ["항생제"]


async def test_되묻기를_이어받는_턴은_모델을_부르지_않는다():
    llm = llm_for("T14-2med")

    ran = await run_case(BY_ID["T14-2med"], client=llm)

    assert len(llm.seen) == 1  # 첫 턴의 모델 한 번뿐이다
    assert [turn.result.model_calls for turn in ran.turns] == [1, 0, 0]


# ── 어긋난 기록은 실패로 잡힌다 ──
async def test_묻는_턴에_쓰기가_있으면_실패다():
    ran = await run_case(BY_ID["T13"], client=llm_for("T13"))
    first, second = ran.turns
    bad = replace(ran, turns=(replace(first, writes=second.writes), second))

    assert any("0건 이하" in item for item in common(bad).failures)


async def test_중단_시각이_답한_턴의_now_가_아니면_실패다():
    ran = await run_case(BY_ID["T13"], client=llm_for("T13"))
    first, second = ran.turns
    [(schedule_id, _)] = second.writes
    bad = replace(ran, turns=(first, replace(second, writes=((schedule_id, first.now),))))

    assert any("now" in item for item in common(bad).failures)


async def test_확인_없이_쓰면_실패다():
    # 중단 확인을 묻지 않은 턴(첫 턴)이 곧바로 썼다
    ran = await run_case(BY_ID["T13"], client=llm_for("T13"))
    first, second = ran.turns
    bad = replace(ran, turns=(replace(first, writes=second.writes), replace(second, writes=())))

    assert common(bad).failures
    assert med_stop(answer="yes")(bad).failures


async def test_네_라고_했는데_안_멈췄으면_실패다():
    ran = await run_case(BY_ID["T13"], client=llm_for("T13"))
    first, second = ran.turns
    bad = replace(ran, turns=(first, replace(second, writes=())))

    assert any("중단 쓰기" in item for item in med_stop(answer="yes")(bad).failures)


async def test_엉뚱한_코스를_멈추면_실패다():
    from uuid import UUID

    ran = await run_case(BY_ID["T13"], client=llm_for("T13"))
    first, second = ran.turns
    bad = replace(ran, turns=(first, replace(second, writes=((UUID(int=9), second.now),))))

    assert any("엉뚱한 코스" in item for item in med_stop(answer="yes")(bad).failures)


async def test_먹였어에_쓰기가_있으면_실패다():
    ran = await run_case(BY_ID["T07"], client=llm_for("T07"))
    ran.world.medication.writes.append((ran.world.courses[0].id, ran.turns[0].now))

    assert check_on_screen()(ran).failures


async def test_먹였어에_다른_문구가_나가면_실패다():
    ran = await run_case(BY_ID["T07"], client=llm_for("T07"))
    wrong = replace(ran.result, readouts=(Readout(kind="notice", body="먹인 걸로 기록했어요"),))

    assert check_on_screen()(replace(ran, result=wrong)).failures


async def test_먹였어에_코스_ref_가_없으면_실패다():
    # 문구는 맞아도 화면이 열 코스가 없다
    ran = await run_case(BY_ID["T07"], client=llm_for("T07"))
    bare = replace(ran.result, readouts=(READOUTS.render("dose.check_on_screen"),))

    assert check_on_screen()(ran).failures == []
    assert check_on_screen()(replace(ran, result=bare)).failures


async def test_먹였어에_진행_중이_아닌_코스_ref_가_있으면_실패다():
    from uuid import UUID

    ran = await run_case(BY_ID["T07"], client=llm_for("T07"))
    [readout] = ran.result.readouts
    other = replace(
        readout, source_refs=(*readout.source_refs, Ref("medication_schedule", UUID(int=9)))
    )

    assert check_on_screen()(replace(ran, result=replace(ran.result, readouts=(other,)))).failures


async def test_표에_안_걸린_현황_질문은_모델_한_번이고_같은_사실을_낸다():
    # 라이브에서만 모델이 새 출구를 고르는지 보이므로, 판정이 모델 1회를 기대할 수 있어야 한다
    ran = await run_case(BY_ID["T10-count"], client=llm_for("T10-count"))
    base = await run_case(BY_ID["T10"], client=llm_for("T10"))

    assert ran.result.model_calls == 1
    assert [r.body for r in ran.result.readouts] == [r.body for r in base.result.readouts]
    assert dose_status(model_calls=1)(ran).failures == []


async def test_dose_status_는_모델_횟수가_다르면_실패다():
    once = await run_case(BY_ID["T10-count"], client=llm_for("T10-count"))
    zero = await run_case(BY_ID["T10"], client=llm_for("T10"))

    assert dose_status()(zero).failures == []
    assert dose_status()(once).failures  # 기본은 모델 0회를 기대한다
    assert dose_status(model_calls=1)(zero).failures


async def test_dose_status_가_허가_문구를_내면_실패다():
    ran = await run_case(BY_ID["T11"], client=llm_for("T11"))
    body = ran.result.readouts[0].body + " 지금 먹여도 돼요"
    wrong = replace(ran.result, readouts=(Readout(kind="dose_status", body=body),))

    assert dose_status()(replace(ran, result=wrong)).failures


async def test_dose_status_가_체크_안_된_회차를_안_먹었다고_하면_실패다():
    ran = await run_case(BY_ID["T10"], client=llm_for("T10"))
    body = "오늘 점심은 안 먹였어요"
    wrong = replace(ran.result, readouts=(Readout(kind="dose_status", body=body),))

    assert len(dose_status()(replace(ran, result=wrong)).failures) >= 2


async def test_투약의뢰서가_서명_문구_없이_나가면_실패다():
    ran = await run_case(BY_ID["T18"], client=llm_for("T18"))
    body = "\n".join(ran.result.readouts[0].body.split("\n")[:-1])
    wrong = replace(ran.result, readouts=(Readout(kind="med_notice_form", body=body),))

    assert handoff_notice()(replace(ran, result=wrong)).failures


async def test_응급_카드에_연락처가_있으면_실패다():
    ran = await run_case(BY_ID["T19"], client=llm_for("T19"))
    body = ran.result.readouts[0].body + "\n• 보호자 연락처 : 010-0000-0000"
    wrong = replace(ran.result, readouts=(Readout(kind="emergency_card", body=body),))

    verdict = common(replace(ran, result=wrong, turns=()))

    assert any("연락처" in item for item in verdict.failures)


async def test_조회_실패를_없음으로_적으면_실패다():
    ran = await run_case(BY_ID["T19-safetyfail"], client=llm_for("T19-safetyfail"))
    lines = ran.result.readouts[0].body.split("\n")
    body = "\n".join([*lines[:2], "• 알레르기 : 없음", *lines[2:]])
    wrong = replace(ran.result, readouts=(Readout(kind="emergency_card", body=body),))

    assert emergency_card(safety_ok=False)(replace(ran, result=wrong)).failures


async def test_Turn_은_저장소_쓰기_증가분만_담는다():
    ran = await run_case(BY_ID["T14-2med"], client=llm_for("T14-2med"))

    assert [len(turn.writes) for turn in ran.turns] == [0, 0, 1]
    assert isinstance(ran.turns[0], Turn)
