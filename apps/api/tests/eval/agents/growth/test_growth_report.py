"""report.py 의 지표 계산 — 지어낸 결과 줄로 본다. 모델을 부르지 않는다."""

from tests.eval.agents.growth.report import metrics, p95, passed


def record(**override):
    base = {
        "case": "T01/C48",
        "golden": False,
        "attempt": 1,
        "outcome": "PASS",
        "model_calls": 1,
        "chat_calls": 3,
        "tokens": {"prompt_tokens": 100, "completion_tokens": 10},
        "ms": 1000,
        "doc_hit": True,
        "failures": [],
        "gaps": [],
        "notes": [],
    }
    return {**base, **override}


def test_p95_는_올림_분위다():
    assert p95(list(range(1, 21))) == 19
    assert p95([]) is None


def test_문서_행_적중률은_볼_일이_있는_run_만_센다():
    m = metrics([record(doc_hit=True), record(doc_hit=False), record(doc_hit=None, model_calls=0)])
    assert (m["doc_hit_rate"], m["doc_hit_n"]) == (0.5, 2)


def test_모델을_부르지_않은_run_은_p95_에_넣지_않는다():
    m = metrics([record(model_calls=0, ms=50_000), record(ms=2_000)])
    assert m["p95_ms"] == 2_000


def test_평가_표현_fail_이_하나라도_있으면_게이트를_못_넘는다():
    m = metrics([record(failures=["공통1 평가 표현 ('또래보다',) — 출력 검증이 뺐어야 한다"])])
    assert m["evaluative_fail"] == 1 and not passed(m)


def test_골든이_하나라도_FAIL_이면_게이트를_못_넘는다():
    m = metrics([record(golden=True, outcome="FAIL"), record(golden=True, outcome="GAP")])
    assert (m["golden_pass"], m["golden_n"]) == (1, 2) and not passed(m)


def test_다_지키면_게이트를_넘는다():
    m = metrics([record(golden=True), record(doc_hit=True, ms=3_000)])
    assert passed(m)
