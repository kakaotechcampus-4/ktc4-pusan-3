"""phases.py 의 전이표 구조. 설계 초안이라 실행 경로는 없고, 두 표가 서로 섞이지 않는지만 본다."""

from app.agents.phases import CURATOR_TRANSITIONS, RUN_TRANSITIONS, PipelinePhase

_CURATOR = {
    PipelinePhase.CURATOR_EMBED,
    PipelinePhase.CURATOR_LINK,
    PipelinePhase.CURATOR_RECOMPUTE,
}


def _targets(table: dict) -> set:
    return {nxt for nexts in table.values() for nxt in nexts} - {None}


def test_모든_Phase_는_두_전이표_중_한_곳에만_있다() -> None:
    run, curator = set(RUN_TRANSITIONS), set(CURATOR_TRANSITIONS)

    assert run.isdisjoint(curator)
    assert run | curator == set(PipelinePhase)


def test_run_은_Curator_로_넘어가지_않는다() -> None:
    # Curator 는 Memory commit 직후 백그라운드로 따로 뜬다. run 의 끝은 Curator 를 기다리지 않는다
    assert _targets(RUN_TRANSITIONS).isdisjoint(_CURATOR)


def test_각_표의_다음_단계는_같은_표_안에_있다() -> None:
    assert _targets(RUN_TRANSITIONS) <= set(RUN_TRANSITIONS)
    assert _targets(CURATOR_TRANSITIONS) <= set(CURATOR_TRANSITIONS)


def test_run_의_끝은_FINALIZE_하나다() -> None:
    # 끝 신호(Done · Partial · Failed)가 나가는 곳
    ends = [phase for phase, nexts in RUN_TRANSITIONS.items() if None in nexts]

    assert ends == [PipelinePhase.FINALIZE]
