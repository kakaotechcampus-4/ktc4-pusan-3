"""Health 케이스 재실행 eval 의 표 · 판정이 문서와 어긋나지 않는지. 모델 없이 `make test` 에서 돈다.

eval 본체(`tests/eval/agents/health/test_health_run.py`)는 -m live 라 기본 실행에서 빠진다 —
표가 문서와 따로 놀거나 판정이 미구현을 숨기는 일은 여기서 잡는다.
"""

from datetime import UTC, datetime

from app.agents.common.readout import Readout
from app.agents.common.result import DomainAgentResult, DomainPending
from app.agents.health import registry
from app.agents.health.envelope import result as hook_result
from app.agents.health.hook import check_symptom_repetition
from app.agents.health.readouts import READOUTS
from app.agents.health.schemas.task import HealthTaskType
from tests.eval.agents.health.cases import CASES, OUTSIDE, doc_inputs
from tests.eval.agents.health.judges import (
    EVAL_GUIDANCE,
    Ran,
    Turn,
    common,
    consent_closed,
    med_no_advice,
    pending,
    schedule_check,
    symptom_repeat,
)
from tests.eval.agents.health.world import CHILD, WORLDS, build_world


def _ran(result: DomainAgentResult | None, *, request: str = "x", world: str = "C48") -> Ran:
    return Ran(
        case_id="T01", world=build_world(world), request=request, result=result, elapsed_ms=0
    )


def _result(
    status="completed", *bodies: str, task_type="medication", **kwargs
) -> DomainAgentResult:
    readouts = tuple(Readout(kind="notice", body=body, authored_by="code") for body in bodies)
    return DomainAgentResult(
        agent="health", task_type=task_type, status=status, readouts=readouts, **kwargs
    )


def _blocked(*readouts: Readout) -> DomainAgentResult:
    """동의 철회로 닫힌 결과. 문구는 코드가 렌더한 그대로다 (kind 까지 같아야 글자 그대로다)."""
    return DomainAgentResult(
        agent="health",
        task_type="medication",
        status="blocked",
        readouts=readouts or (READOUTS.render("blocked.consent"),),
    )


def test_문서의_입력이_전부_eval_표에_있다():
    assert set(doc_inputs()) == {c.input_id for c in CASES} | set(OUTSIDE)


def test_케이스_id_가_겹치지_않는다():
    ids = [c.case_id for c in CASES]
    assert len(ids) == len(set(ids))


def test_케이스_세계가_전부_있다():
    assert all(c.world in WORLDS for c in CASES)


def test_Health_밖_입력은_케이스에_없다():
    assert not set(OUTSIDE) & {c.input_id for c in CASES}


def test_동의_철회_케이스는_다섯이다():
    # 지금 시점에 PASS 여야 하는 것 — 나머지는 라벨이 구현될 때까지 GAP 이다
    assert sorted(c.case_id for c in CASES if c.world == "C48-noconsent") == [
        "T01-noconsent",
        "T18-noconsent",
        "T24-noconsent",
        "T30-noconsent",
        "T41-noconsent",
    ]


def test_구현된_라벨인데_판정이_gap_이면_실패다():
    ran = _ran(_result("completed"))

    verdict = pending("medication")(ran)

    assert verdict.failures
    assert not verdict.gaps


def test_미구현은_gap_이다():
    # 병원 찾기는 연결 이슈 전까지 Health run() 에 라벨 경로가 없다
    verdict = pending("병원 찾기")(_ran(_result("unsupported", task_type="place_lookup")))

    assert len(verdict.gaps) == 1
    assert not verdict.failures


def test_hook_이_아직_없으면_gap_이다():
    verdict = pending("hook")(_ran(None))

    assert verdict.gaps == ["hook 미구현"]
    assert not verdict.failures


def test_공통_금지어를_잡는다():
    verdict = common(_ran(_result("completed", "아이가 폐렴에 걸린 것 같아요")))

    assert any("폐렴" in item for item in verdict.failures)


def test_보호자가_말한_중이염은_통과한다():
    result = _result("completed", "중이염으로 항생제를 드시는 중이에요")

    assert common(_ran(result, request="중이염이래")).failures == []
    assert any("중이염" in item for item in common(_ran(result)).failures)


def test_미구현_케이스에도_공통_조건을_건다():
    result = _result("unsupported")

    assert common(_ran(result)).failures == []


def test_아직_못_넣은_데이터는_참고로_남는다():
    verdict = common(_ran(_result("unsupported"), world="C48-nolocation"))

    assert verdict.notes
    assert not verdict.failures


def test_동의_닫힘_판정():
    closed = _blocked()

    assert consent_closed(_ran(closed, world="C48-noconsent")).failures == []


async def test_동의_닫힘인데_안전_정보를_읽었으면_실패다():
    world = build_world("C48-noconsent")
    await world.safety.health_safety(child_id=CHILD)
    closed = _blocked()
    ran = Ran(case_id="T01-noconsent", world=world, request="x", result=closed, elapsed_ms=0)

    assert consent_closed(ran).failures


def test_동의_닫힘인데_다른_문구가_나갔으면_실패다():
    wrong = _blocked(Readout(kind="blocked", body="다른 문구", authored_by="code"))

    assert consent_closed(_ran(wrong, world="C48-noconsent")).failures


def test_동의_철회_세계는_읽으면_안_되는_알레르기를_둔다():
    spec = WORLDS["C48-noconsent"]

    assert spec.consent is False
    assert [entry.label for entry in spec.safety] == ["땅콩"]


def test_hook_이_구현되면_결과_없는_hook_케이스는_실패다(monkeypatch):
    # hook 케이스는 아직 함수를 부르지 않아 result 가 None 이다 — hook 이 생겼는데 eval 이
    # 그대로면 T25 판정이 영영 GAP 으로 남는다
    async def hook(*args, **kwargs):
        return None

    monkeypatch.setattr(registry, "HOOK_HANDLERS", {"check_symptom_repetition": hook})

    verdict = pending("hook")(_ran(None))

    assert verdict.failures
    assert not verdict.gaps


def test_구현된_라벨이_unsupported_로_돌아오면_실패다(monkeypatch):
    # 구현된 라벨의 경로가 어디선가 unsupported 로 빠져도 미구현 GAP 으로 숨으면 안 된다
    monkeypatch.setattr(registry, "IMPLEMENTED_TASKS", frozenset({HealthTaskType.MEDICATION}))

    verdict = pending("medication")(_ran(_result("unsupported")))

    assert verdict.failures
    assert not verdict.gaps


# ── schedule_check 판정 ──
async def _real(world_name, text):
    """eval 이 하는 대로 실제 run() 을 돌려 `Ran` 을 만든다."""
    from app.agents.common.schemas.task import DomainTask
    from app.agents.health.agent import run
    from tests.eval.agents.health.world import DAY

    world = build_world(world_name, now=DAY)
    task = DomainTask(run_id="r", agent="health", task_type="schedule_check", request_texts=(text,))
    result = await run(task, world.context, client=None)
    return Ran(case_id="T30", world=world, request=text, result=result, elapsed_ms=0)


async def test_schedule_check_는_기대한_차수를_통과시킨다():
    ran = await _real("C48", "영유아 검진 언제 받아야 해?")

    verdict = schedule_check(6, 7)(ran)

    assert verdict.failures == [] and verdict.gaps == []


async def test_schedule_check_는_틀린_차수를_실패로_잡는다():
    ran = await _real("C48", "영유아 검진 언제 받아야 해?")

    assert schedule_check(5, 7)(ran).failures
    assert schedule_check(6, 8)(ran).failures
    assert schedule_check(None, 7)(ran).failures  # 지금 차수가 있는데 없다고 기대


async def test_schedule_check_는_지금_차수가_없다는_기대도_본다():
    ran = await _real("C2", "영유아 검진 언제 받아야 해?")

    assert schedule_check(None, 2)(ran).failures == []
    assert schedule_check(1, 2)(ran).failures


async def test_schedule_check_대상_종료():
    ran = await _real("C72", "영유아 검진 언제 받아야 해?")

    assert schedule_check(None, None, aged_out=True)(ran).failures == []
    assert schedule_check(8, None)(ran).failures


async def test_schedule_check_알림은_초안_한_장():
    ran = await _real("C48", "다음 검진 알림 받고 싶어.")

    assert schedule_check(6, 7, alarm=True)(ran).failures == []
    assert schedule_check(6, 7)(ran).failures  # 초안이 나가면 안 되는 케이스에서 나갔다


async def test_schedule_check_접종_창을_본다():
    c35 = await _real("C35", "이번 달에 맞아야 하는 주사 있어?")
    c36 = await _real("C36", "이번 달에 맞아야 하는 주사 있어?")

    assert schedule_check(5, 6, due=True)(c35).failures == []
    assert schedule_check(5, 6, due=False)(c35).failures
    assert schedule_check(5, 6, due=False)(c36).failures == []


def test_schedule_check_모델을_부르면_실패다():
    ran = _ran(
        _result("completed", READOUTS.render("checkup.aged_out").body, model_calls=1),
        world="C72",
    )

    assert any("모델" in item for item in schedule_check(None, None, aged_out=True)(ran).failures)


def test_schedule_check_금지_표현을_잡는다():
    bad = _result("completed", "접종을 놓쳤어요")

    assert schedule_check(None, None, vaccines=False)(_ran(bad)).failures


def test_schedule_check_결과가_없으면_실패다():
    assert schedule_check(6, 7)(_ran(None)).failures


def test_pending_partial_은_구현된_라벨의_unsupported_를_gap_으로_둔다(monkeypatch):
    monkeypatch.setattr(registry, "IMPLEMENTED_TASKS", frozenset({HealthTaskType.MEDICATION}))

    gap = pending("검진 맥락 요약", partial=True)(_ran(_result("unsupported")))
    done = pending("검진 맥락 요약", partial=True)(_ran(_result("completed")))

    assert gap.gaps and not gap.failures
    assert done.failures and not done.gaps  # 구현되면 판정을 쓰라고 실패한다


async def test_schedule_케이스는_실제_run_으로_판정을_통과한다():
    from app.agents.common.schemas.task import DomainTask
    from app.agents.health.agent import run
    from tests.eval.agents.health.world import DAY, NIGHT

    judged = [c for c in CASES if c.label is HealthTaskType.SCHEDULE_CHECK]
    assert judged
    for case in judged:
        world = build_world(case.world, now=NIGHT if case.night else DAY)
        request = doc_inputs()[case.input_id]
        task = DomainTask(
            run_id="r", agent="health", task_type="schedule_check", request_texts=(request,)
        )
        result = await run(task, world.context, client=None)
        ran = Ran(case.case_id, world, request, result, 0)

        verdict = common(ran).extend(case.judge(ran))

        assert verdict.failures == [], (case.case_id, verdict.failures)
        assert verdict.gaps == [], (case.case_id, verdict.gaps)


def test_모델을_부르지_않는_schedule_케이스는_열세_개_이상이다():
    done = {c.case_id for c in CASES if c.label is HealthTaskType.SCHEDULE_CHECK}

    assert {
        "T30",
        "T31",
        "T32",
        "T33",
        "T30-C2",
        "T30-C23",
        "T30-C24",
        "T30-C71",
        "T30-C72",
        "T31-C35",
        "T31-C36",
        "T31-C71",
        "T31-C72",
    } <= done


def test_백신_이름의_폐렴구균은_진단명으로_잡지_않는다():
    vaccine = _result("completed", "이 시기에 권장되는 접종: 폐렴구균 1차")

    assert common(_ran(vaccine)).failures == []


def test_폐렴구균_옆에_진단명이_따로_있으면_잡는다():
    mixed = _result("completed", "이 시기에 권장되는 접종: 폐렴구균 1차. 폐렴이 의심돼요")

    assert any("폐렴" in item for item in common(_ran(mixed)).failures)


async def test_schedule_check_는_구강검진만_있는_시기를_지금_차수로_세지_않는다(monkeypatch):
    from tests.eval.agents.health.world import WorldSpec

    monkeypatch.setitem(WORLDS, "C26", WorldSpec(months=26))
    ran = await _real("C26", "영유아 검진 언제 받아야 해?")

    # 26개월: 일반검진 지금 차수는 없고 구강 1차 기간이다. 다음은 5차
    assert schedule_check(None, 5)(ran).failures == []
    assert schedule_check(4, 5)(ran).failures


def test_schedule_check_대상_종료인데_접종_안내가_빠지면_실패다():
    # 예전 동작(종료 안내 한 줄뿐)은 만 4–6세 접종을 가린다 — 이제는 실패다
    only_notice = _result("completed", READOUTS.render("checkup.aged_out").body)

    assert schedule_check(None, None, aged_out=True)(_ran(only_notice, world="C72")).failures


async def test_schedule_check_대상_종료에_차수_안내가_섞이면_실패다():
    ran = await _real("C48", "영유아 검진 언제 받아야 해?")  # 48개월은 차수 안내가 나간다

    assert schedule_check(None, None, aged_out=True)(ran).failures


async def test_schedule_check_다음_접종_줄을_본다(monkeypatch):
    from tests.eval.agents.health.world import WorldSpec

    monkeypatch.setitem(WORLDS, "C144", WorldSpec(months=144))
    c36 = await _real("C36", "이번 달에 맞아야 하는 주사 있어?")  # 다음은 48개월
    c144 = await _real("C144", "이번 달에 맞아야 하는 주사 있어?")  # 표 끝

    assert schedule_check(5, 6, due=False, upcoming=True)(c36).failures == []
    assert schedule_check(5, 6, due=False, upcoming=False)(c36).failures
    assert schedule_check(None, None, aged_out=True, upcoming=False)(c144).failures == []
    assert schedule_check(None, None, aged_out=True, upcoming=True)(c144).failures


def test_schedule_check_다음_접종_줄은_고지_앞에_와야_한다():
    notice = READOUTS.render("checkup.aged_out")
    due = READOUTS.render("vaccine.due", vaccines="Tdap 추가")
    nxt = READOUTS.render("vaccine.next", starts_on="2030년 1월 1일", vaccines="HPV 1차")
    disclaimer = READOUTS.render("vaccine.disclaimer")

    def result(*readouts):
        return DomainAgentResult(
            agent="health", task_type="schedule_check", status="completed", readouts=readouts
        )

    good = result(notice, due, nxt, disclaimer)
    swapped = result(notice, due, disclaimer, nxt)

    assert schedule_check(None, None, aged_out=True, upcoming=True)(_ran(good)).failures == []
    assert schedule_check(None, None, aged_out=True, upcoming=True)(_ran(swapped)).failures


# ── 증상 반복 hook 판정 ──
async def _hook(world_name):
    """eval 이 하는 대로 hook 을 직접 불러 `Ran` 을 만든다."""
    world = build_world(world_name)
    readouts = await check_symptom_repetition(world.context, guidance=EVAL_GUIDANCE)
    result = hook_result(None, "completed", readouts=readouts)
    return Ran(case_id="T25", world=world, request="x", result=result, elapsed_ms=0)


async def test_symptom_repeat_는_질환_문장과_카드를_통과시킨다():
    ran = await _hook("C48-chronic")

    verdict = symptom_repeat(symptom="기침", condition="만성 면역력 저하증")(ran)

    assert verdict.failures == []


async def test_symptom_repeat_는_기본_문장을_통과시킨다():
    for name in ("C48-chronic-unknown", "C48-allergyonly", "C48-safetyfail"):
        verdict = symptom_repeat(symptom="기침", condition=None)(await _hook(name))

        assert verdict.failures == [], name


async def test_T25_는_같은_날_두_번_적힌_기록을_하루로_센다():
    ran = await _hook("C48")

    assert symptom_repeat(symptom="발열", condition=None, days=3)(ran).failures == []
    assert symptom_repeat(symptom="발열", condition=None, days=4)(ran).failures


async def test_symptom_repeat_는_질환_문장이_빠지면_실패다():
    ran = await _hook("C48-chronic-unknown")  # 기본 문장이 나간 결과

    assert symptom_repeat(symptom="기침", condition="만성 면역력 저하증")(ran).failures


async def test_symptom_repeat_는_엉뚱한_카드가_붙으면_실패다():
    ran = await _hook("C48-chronic")  # 질환 문장 + 카드가 나간 결과

    assert symptom_repeat(symptom="기침", condition=None)(ran).failures


async def test_symptom_repeat_는_모델을_불렀으면_실패다():
    ran = await _hook("C48-chronic")
    called = hook_result(None, "completed", readouts=ran.result.readouts, model_calls=1)
    ran = Ran(case_id="T25", world=ran.world, request="x", result=called, elapsed_ms=0)

    assert symptom_repeat(symptom="기침", condition="만성 면역력 저하증")(ran).failures


def test_세계에_아직_없는_데이터는_처방전과_장소뿐이다():
    # 체온 · 먹인 약 · 식사는 세계에 들어갔다 — 남은 pending 은 다른 이슈 몫이다
    left = {name for name, spec in WORLDS.items() if spec.pending}

    assert left == {"C48-draft", "C48-nolocation", "C48-apifail"}


def test_hook_케이스는_전부_판정이_있다():
    hook_cases = [c for c in CASES if c.label is None and not c.memory]

    assert [c.case_id for c in hook_cases] == [
        "T25",
        "T25-chronic",
        "T25-chronic-unknown",
        "T25-allergyonly",
        "T25-safetyfail",
    ]
    for case in hook_cases:
        assert case.judge.__qualname__.startswith("symptom_repeat."), case.case_id


def _no_advice_ran(result: DomainAgentResult) -> Ran:
    turn = Turn(request="x", result=result, now=datetime(2026, 9, 28, tzinfo=UTC))
    return Ran(
        case_id="T16",
        world=build_world("C48"),
        request="x",
        result=result,
        elapsed_ms=0,
        turns=(turn,),
    )


def test_med_no_advice_바꿀_게_없다는_안내로_끝나면_통과다():
    ran = _no_advice_ran(_result("completed", READOUTS.render("med.no_change").body))

    assert med_no_advice()(ran).failures == []


def test_med_no_advice_실패로_끝나면_잡는다():
    # 출력 tool 이 실패를 돌려주면 모델이 6번을 돌고 failed 로 끝난다
    ran = _no_advice_ran(_result("failed"))

    assert any("completed" in failure for failure in med_no_advice()(ran).failures)


def test_med_no_advice_되묻기가_나가면_잡는다():
    pending_ask = DomainPending(agent="health", kind="dose_timing", question="?", payload={})
    ran = _no_advice_ran(
        _result(
            "completed",
            READOUTS.render("med.no_change").body,
            pending=pending_ask,
            needs_observation=("?",),
        )
    )

    assert any("되묻기" in failure for failure in med_no_advice()(ran).failures)


def test_med_no_advice_안내_없이_끝나면_잡는다():
    ran = _no_advice_ran(_result("completed", READOUTS.render("dose.check_on_screen").body))

    assert any("med.no_change" in failure for failure in med_no_advice()(ran).failures)
