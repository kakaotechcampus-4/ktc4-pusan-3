"""판정기 측정셋 · 판정 규칙 (#816). API 를 부르지 않아 기본 테스트(make test)에서 돈다."""

import pytest

from tests.eval.agents.curator.judge_cases import JudgeCase, evalset_hash, grade, load_cases
from tests.unit.agents.common.prompt_history import doc_path, parse_history


def test_측정_입력은_운영에서_판정기가_받는_물음만이다() -> None:
    cases = load_cases()
    assert len({case.id for case in cases}) == len(cases), "입력 id 가 겹치면 회차가 섞인다"
    for case in cases:
        assert case.candidates, f"{case.id} 후보가 없으면 연결 단계가 판정기 없이 새로 만든다"
        names = ["".join(c.split()) for c in case.candidates]
        assert "".join(case.subject.split()) not in names, f"{case.id} 이름이 같으면 코드가 붙인다"
        assert case.expected == "none" or case.expected in case.candidates, case.id


def test_측정_이력_마지막_행은_지금_평가셋으로_쟀다() -> None:
    """평가셋만 바꾸고 다시 재지 않으면, 다음 프롬프트 변경이 다른 평가셋의 행과 비교된다."""
    last = parse_history(doc_path("curator_judge").read_text(encoding="utf-8"))[-1]
    current = f"`{evalset_hash(load_cases())}`"
    assert current in last.condition, (
        f"평가셋이 {last.version} 을 잰 뒤 바뀌었다(지금 {current}). "
        "데이터 파일 · judge_cases.py · 연결 단계의 이름 비교와 MAX_CANDIDATES 가 "
        "평가셋을 정한다. 바꾸기 전 프롬프트를 지금 평가셋으로 다시 재서 "
        "CURATOR_JUDGE_EVAL.md §1 에 행을 더한다."
    )


DUBU = JudgeCase("t:1/original", "food", "두부", ("순두부", "연두부"), "none")
MELON = JudgeCase("t:2/original", "food", "메론", ("멜론", "수박"), "멜론")


@pytest.mark.parametrize(
    ("case", "choice", "verdict"),
    [
        (MELON, "멜론", "pass"),
        (DUBU, "none", "pass"),
        (MELON, "수박", "c1"),  # 기대와 다른 후보 — 잘못 합침
        (DUBU, "순두부", "c1"),  # 같은 후보가 없는데 고름 — 잘못 합침
        (MELON, "none", "general"),  # 놓침
        (MELON, "uncertain", "general"),
        (DUBU, "uncertain", "general"),
        (MELON, "참외", "general"),  # 후보 목록에 없는 답은 연결 단계가 보류한다
    ],
)
def test_다른_후보를_고르면_C1_이고_놓침_미룸_엉뚱한_답은_일반_실패다(case, choice, verdict):
    assert grade(case, choice)[0] == verdict
