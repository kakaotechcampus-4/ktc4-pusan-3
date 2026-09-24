"""임계값 실험 데이터 읽기. 데이터는 옆의 .txt 파일에 있다.

    pairs_tune.txt        선정용 쌍 — 임계값을 고른다
    pairs_holdout.txt     검증용 쌍 — 고른 임계값을 평가만 한다
    scenarios_holdout.txt 검증용 시나리오 — 순서가 있는 관찰 흐름
    pairs_ambiguous.txt   팀이 정해야 하는 쌍 — 점수에 넣지 않고 유사도만 보고한다

파일 규칙(정답 값, 선정용 · 검증용 subject 겹침 등)은 test_pairs_data.py 가 확인한다.
"""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast, get_args

from app.agents.curator.embedding.ports import CURATOR_DOMAINS, CuratorDomain

HERE = Path(__file__).parent
TUNE_PATH = HERE / "pairs_tune.txt"
HOLDOUT_PATH = HERE / "pairs_holdout.txt"
SCENARIOS_PATH = HERE / "scenarios_holdout.txt"
AMBIGUOUS_PATH = HERE / "pairs_ambiguous.txt"

Label = Literal["same", "near", "unrelated"]
AmbiguousKind = Literal["A", "B", "C", "D", "E", "F", "G"]
LABELS: tuple[Label, ...] = get_args(Label)
AMBIGUOUS_KINDS: tuple[AmbiguousKind, ...] = get_args(AmbiguousKind)


@dataclass(frozen=True)
class Pair:
    domain: CuratorDomain
    label: Label
    a: str
    b: str
    line: int  # 파일의 줄 번호. 실패 메시지에 쓴다


@dataclass(frozen=True)
class AmbiguousPair:
    kind: AmbiguousKind
    domain: CuratorDomain
    a: str
    b: str
    line: int


@dataclass(frozen=True)
class Step:
    polarity: int
    subject: str


@dataclass(frozen=True)
class Scenario:
    id: str
    domain: CuratorDomain
    steps: tuple[Step, ...]
    groups: tuple[tuple[int, ...], ...]  # 같은 Profile 로 묶여야 하는 관찰 번호(1부터)


class DataError(ValueError):
    """데이터 파일 형식이 틀렸다. 메시지에 파일과 줄 번호를 적는다."""


def _lines(path: Path) -> list[tuple[int, str]]:
    """주석(#)과 빈 줄을 뺀 (줄 번호, 내용)."""
    out = []
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        text = raw.split("#", 1)[0].strip()
        if text:
            out.append((number, text))
    return out


def _cells(path: Path, number: int, text: str, count: int) -> list[str]:
    cells = [cell.strip() for cell in text.split("|")]
    if len(cells) != count or not all(cells):
        raise DataError(f"{path.name}:{number} 칸이 {count}개여야 한다: {text!r}")
    return cells


def _domain(path: Path, number: int, value: str) -> CuratorDomain:
    if value not in CURATOR_DOMAINS:
        raise DataError(f"{path.name}:{number} 도메인은 {CURATOR_DOMAINS} 중 하나: {value!r}")
    return cast(CuratorDomain, value)


def load_pairs(path: Path) -> list[Pair]:
    pairs = []
    for number, text in _lines(path):
        domain, label, a, b = _cells(path, number, text, 4)
        if label not in LABELS:
            raise DataError(f"{path.name}:{number} 정답은 {LABELS} 중 하나: {label!r}")
        pairs.append(Pair(_domain(path, number, domain), cast(Label, label), a, b, number))
    return pairs


def load_ambiguous(path: Path = AMBIGUOUS_PATH) -> list[AmbiguousPair]:
    pairs = []
    for number, text in _lines(path):
        kind, domain, a, b = _cells(path, number, text, 4)
        if kind not in AMBIGUOUS_KINDS:
            raise DataError(f"{path.name}:{number} 유형은 {AMBIGUOUS_KINDS} 중 하나: {kind!r}")
        domain_ = _domain(path, number, domain)
        pairs.append(AmbiguousPair(cast(AmbiguousKind, kind), domain_, a, b, number))
    return pairs


_HEADER = re.compile(r"^\[(S\d+)\]\s+(\S+)$")
_STEP = re.compile(r"^([+-]?[01])\s+(.+)$")


def load_scenarios(path: Path = SCENARIOS_PATH) -> list[Scenario]:
    scenarios: list[Scenario] = []
    current: tuple[str, CuratorDomain] | None = None
    steps: list[Step] = []
    for number, text in _lines(path):
        if header := _HEADER.match(text):
            if current is not None:
                raise DataError(f"{path.name}:{number} 앞 시나리오에 expect 가 없다")
            current, steps = (header[1], _domain(path, number, header[2])), []
        elif text.startswith("expect:"):
            if current is None:
                raise DataError(f"{path.name}:{number} 시나리오 머리([S번호] 도메인)가 없다")
            groups = tuple(
                tuple(int(n) for n in group.split())
                for group in text.removeprefix("expect:").split("|")
            )
            numbers = sorted(n for group in groups for n in group)
            if numbers != list(range(1, len(steps) + 1)):
                raise DataError(
                    f"{path.name}:{number} expect 가 관찰 1~{len(steps)} 을 한 번씩 덮어야 한다"
                )
            scenarios.append(Scenario(current[0], current[1], tuple(steps), groups))
            current = None
        elif step := _STEP.match(text):
            if current is None:
                raise DataError(f"{path.name}:{number} 시나리오 머리([S번호] 도메인)가 없다")
            steps.append(Step(int(step[1]), step[2].strip()))
        else:
            raise DataError(f"{path.name}:{number} 알 수 없는 줄: {text!r}")
    if current is not None:
        raise DataError(f"{path.name} 마지막 시나리오에 expect 가 없다")
    return scenarios


def subjects(pairs: list[Pair]) -> set[str]:
    return {s for pair in pairs for s in (pair.a, pair.b)}
