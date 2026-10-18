"""Health 케이스 재실행 eval 의 표 · 판정이 문서와 어긋나지 않는지. 모델 없이 `make test` 에서 돈다.

eval 본체(`tests/eval/agents/health/test_health_run.py`)는 -m live 라 기본 실행에서 빠진다 —
표가 문서와 따로 놀거나 판정이 미구현을 숨기는 일은 여기서 잡는다.
"""

from app.agents.common.readout import Readout
from app.agents.common.result import DomainAgentResult
from app.agents.health import registry
from app.agents.health.readouts import READOUTS
from app.agents.health.schemas.task import HealthTaskType
from tests.eval.agents.health.cases import CASES, OUTSIDE, doc_inputs
from tests.eval.agents.health.judges import Ran, common, consent_closed, pending
from tests.eval.agents.health.world import CHILD, WORLDS, build_world


def _ran(result: DomainAgentResult | None, *, request: str = "x", world: str = "C48") -> Ran:
    return Ran(
        case_id="T01", world=build_world(world), request=request, result=result, elapsed_ms=0
    )


def _result(status="completed", *bodies: str, **kwargs) -> DomainAgentResult:
    readouts = tuple(Readout(kind="notice", body=body, authored_by="code") for body in bodies)
    return DomainAgentResult(
        agent="health", task_type="medication", status=status, readouts=readouts, **kwargs
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
    verdict = pending("medication")(_ran(_result("unsupported")))

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
    verdict = common(_ran(_result("unsupported"), world="C48-2med"))

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
