"""Curator 판정기 측정셋과 판정 — `docs/agents/eval/CURATOR_JUDGE_EVAL.md` §1 을 재는 입력 (#816).

입력 하나 = 판정기를 한 번 부르는 물음(subject · 후보 · 기대 답)이다. 연결 단계(`link_pending`)를
거치지 않고 판정기만 부른다. 측정 이력은 프롬프트 지문을 따라가는 표라서, 지문 밖의 코드(이름 비교 ·
반대 방향 확인 · 후보 추리기)가 결과를 바꾸면 행끼리 견줄 수 없다. 대신 운영에서 판정기가 받는
두 가지 물음을 다 넣는다.

    original · reversed · absent  새 관찰 subject 와 후보 몇 개 (연결 단계 4). 실험 3 · 5 와 같다
    back                           방향을 바꾼 한 후보 물음 (연결 단계 5 반대 방향 확인)
    scale                          Profile 이 쌓인 상태의 새 관찰 — 후보 12 · 20개 (연결 단계 4)

평가셋은 판단이 갈리지 않는 쌍 파일 셋(`PAIR_FILES`) 전부와 `tune_scale.txt` 다. 판단이 갈리는
쌍(`disputed_pairs`)은 점수에 넣지 않는다. 운영에서 판정기가 받지 않는 물음은 뺀다 — 후보가 없거나,
후보 중에 subject 와 이름이 같은 것이 있으면(공백만 다름) 연결 단계가 판정기 없이 정한다.
`tune_scale.txt` 의 Profile 이 `MAX_CANDIDATES` 를 넘는 시나리오(R01)도 뺀다 — 운영은 벡터로 후보를
추려 보여 주는데, 무엇이 남는지는 임베딩 모델이 정해서 판정기만 부르는 이 측정으로는 재현할 수 없다.

입력 id 는 `파일:도메인:이름/물음` 이다. 줄 번호로 만들지 않는다 — `tune_pairs.txt` 에는 판단이
갈리는 쌍이 정해지면 옮겨 오므로(`disputed_pairs.txt` 머리말), 줄이 밀리면 회차 사이에 id 가
다른 물음을 가리킨다. 이름은 모두 가상 데이터라 id 에 실어도 된다.

판정 (`CURATOR_JUDGE_EVAL.md` §2)
    C1    기대와 다른 후보를 골랐다 — 다른 대상을 같다고 판정 (잘못 합침)
    일반  같은 후보가 있는데 none (놓침) · uncertain (미룸) · 후보 목록에 없는 답
"""

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from app.agents.curator.embedding.judge import NONE, RESERVED, UNCERTAIN
from app.agents.curator.embedding.link_step import MAX_CANDIDATES, same_name_key
from app.agents.curator.embedding.ports import CuratorDomain
from tests.eval.agents.curator.datasets import (
    CHECK1_PAIRS_PATH,
    CHECK2_PAIRS_PATH,
    TUNE_PAIRS_PATH,
    TUNE_SCALE_PATH,
    Pair,
    load_pairs,
    load_scale,
)

PAIR_FILES: tuple[Path, ...] = (TUNE_PAIRS_PATH, CHECK1_PAIRS_PATH, CHECK2_PAIRS_PATH)

Verdict = Literal["pass", "c1", "general"]


@dataclass(frozen=True)
class JudgeCase:
    id: str  # tally 의 입력 id — 모듈 머리말
    domain: CuratorDomain
    subject: str
    candidates: tuple[str, ...]
    expected: str  # candidates 중 하나, 또는 none


def _questions(path: Path, pairs: Sequence[Pair]) -> Iterator[JudgeCase]:
    # 방해 후보는 정답 파일이 "다르다" 고 적은 이름만 쓴다 (실험 3 `cases` 와 같다)
    negatives: dict[tuple[str, str], set[str]] = {}
    for p in pairs:
        if p.label != "same":
            negatives.setdefault((p.domain, p.a), set()).add(p.b)
            negatives.setdefault((p.domain, p.b), set()).add(p.a)
    for p in pairs:
        options = tuple(dict.fromkeys([p.b, *sorted(negatives.get((p.domain, p.a), set()))]))
        same = p.label == "same"
        asks: list[tuple[str, str, tuple[str, ...], str]] = [
            ("original", p.a, options, p.b if same else NONE),
            ("reversed", p.a, options[::-1], p.b if same else NONE),
            ("back", p.b, (p.a,), p.a if same else NONE),
        ]
        if same:
            asks.append(("absent", p.a, tuple(o for o in options if o != p.b), NONE))
        for variant, subject, candidates, expected in asks:
            case_id = f"{path.stem}:{p.domain}:{p.a}–{p.b}/{variant}"
            yield JudgeCase(case_id, p.domain, subject, candidates, expected)


def _scale_questions() -> Iterator[JudgeCase]:
    # 후보는 적힌 순서 그대로 — 운영도 저장된 순서(오래된 Profile 먼저)로 보여 준다
    for sc in load_scale(TUNE_SCALE_PATH):
        if len(sc.profiles) > MAX_CANDIDATES:
            continue
        for ask in sc.asks:
            case_id = f"{TUNE_SCALE_PATH.stem}:{sc.id}:{ask.subject}/scale"
            yield JudgeCase(case_id, sc.domain, ask.subject, sc.profiles, ask.expected)


def _asked_in_production(case: JudgeCase) -> bool:
    """연결 단계가 판정기를 부르는 물음인가.

    후보가 없거나 이름이 같은 후보가 있으면 연결 단계가 판정기 없이 정한다.
    """
    name = same_name_key(case.subject)
    return bool(case.candidates) and all(same_name_key(c) != name for c in case.candidates)


def load_cases() -> list[JudgeCase]:
    """측정 입력. 같은 물음(도메인 · subject · 후보 순서)이 두 번 나오면 앞의 것만 둔다 —
    한 쌍의 후보가 하나뿐이면 reversed 는 original 과 같은 물음이다."""
    questions = [q for path in PAIR_FILES for q in _questions(path, load_pairs(path))]
    cases: dict[tuple[str, str, tuple[str, ...]], JudgeCase] = {}
    for case in [*questions, *_scale_questions()]:
        if _asked_in_production(case):
            cases.setdefault((case.domain, case.subject, case.candidates), case)
    return list(cases.values())


def picked(choice: str, candidates: Sequence[str]) -> str | None:
    """판정기 답이 가리키는 후보. 연결 단계(`link_step._pick`)와 같은 규칙이다 —
    목록에 그대로 있거나, 공백만 다른 후보가 딱 하나일 때만 인정한다."""
    if choice in candidates:
        return choice
    loose = [c for c in candidates if same_name_key(c) == same_name_key(choice)]
    return loose[0] if len(loose) == 1 else None


def grade(case: JudgeCase, choice: str) -> tuple[Verdict, str]:
    """(판정, 이유). 이유에는 이름을 싣지 않는다 — 회차 기록(tally)은 입력 id 와 건수만 본다."""
    if choice == UNCERTAIN:
        return "general", "uncertain 으로 미룸"
    if choice == NONE:
        return ("pass", "") if case.expected == NONE else ("general", "같은 후보를 놓침")
    chosen = None if choice in RESERVED else picked(choice, case.candidates)
    if chosen is None:
        return "general", "후보 목록에 없는 답"
    if case.expected != NONE and same_name_key(chosen) == same_name_key(case.expected):
        return "pass", ""
    return "c1", "다른 대상을 같다고 판정 (잘못 합침)"
