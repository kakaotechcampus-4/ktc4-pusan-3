"""실험 데이터 읽기. 데이터는 data/ 폴더에 있다 — 어느 실험에 쓰였는지는 data/README.md.

파일 규칙(정답 값, 조정용 · 확인용 subject 겹침 등)은 test_pairs_data.py 가 확인한다.
"""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast, get_args

from app.agents.curator.embedding.ports import CURATOR_DOMAINS, CuratorDomain

HERE = Path(__file__).parent
RESULTS_DIR = HERE / "results"  # 실험 결과 파일. gitignore 대상이라 이 컴퓨터에만 남는다
DATA_DIR = HERE / "data"
TUNE_PAIRS_PATH = DATA_DIR / "tune_pairs.txt"
TUNE_SCALE_PATH = DATA_DIR / "tune_scale.txt"
CHECK1_PAIRS_PATH = DATA_DIR / "check1_pairs.txt"
CHECK1_ORDERS_PATH = DATA_DIR / "check1_orders.txt"
CHECK2_PAIRS_PATH = DATA_DIR / "check2_pairs.txt"
CHECK2_ORDERS_PATH = DATA_DIR / "check2_orders.txt"
DISPUTED_PAIRS_PATH = DATA_DIR / "disputed_pairs.txt"


def results_dir(experiment: str) -> Path:
    """실험별 결과 폴더 (예: results/exp3). 없으면 만든다."""
    path = RESULTS_DIR / experiment
    path.mkdir(parents=True, exist_ok=True)
    return path


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
class DisputedPair:
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


@dataclass(frozen=True)
class Ask:
    subject: str
    expected: str  # profiles 중 하나, 또는 "none"(새 Profile)


@dataclass(frozen=True)
class ScaleScenario:
    """Profile 이 쌓인 상태에서 새 관찰 하나씩. ask 마다 따로 평가한다."""

    id: str
    domain: CuratorDomain
    profiles: tuple[str, ...]
    asks: tuple[Ask, ...]


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


def load_disputed(path: Path = DISPUTED_PAIRS_PATH) -> list[DisputedPair]:
    pairs = []
    for number, text in _lines(path):
        kind, domain, a, b = _cells(path, number, text, 4)
        if kind not in AMBIGUOUS_KINDS:
            raise DataError(f"{path.name}:{number} 유형은 {AMBIGUOUS_KINDS} 중 하나: {kind!r}")
        domain_ = _domain(path, number, domain)
        pairs.append(DisputedPair(cast(AmbiguousKind, kind), domain_, a, b, number))
    return pairs


_HEADER = re.compile(r"^\[([A-Z]\d+)\]\s+(\S+)$")
_STEP = re.compile(r"^([+-]?[01])\s+(.+)$")


def load_scenarios(path: Path = CHECK1_ORDERS_PATH) -> list[Scenario]:
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


def load_scale(path: Path = TUNE_SCALE_PATH) -> list[ScaleScenario]:
    scenarios: list[ScaleScenario] = []
    current: tuple[str, CuratorDomain] | None = None
    profiles: tuple[str, ...] = ()
    asks: list[Ask] = []

    def close(number: int) -> None:
        if current is None:
            return
        if not profiles or not asks:
            raise DataError(f"{path.name}:{number} {current[0]} 에 profiles 와 ask 가 있어야 한다")
        scenarios.append(ScaleScenario(current[0], current[1], profiles, tuple(asks)))

    number = 0
    for number, text in _lines(path):
        if header := _HEADER.match(text):
            close(number)
            current, profiles, asks = (header[1], _domain(path, number, header[2])), (), []
        elif text.startswith("profiles:"):
            profiles = tuple(p.strip() for p in text.removeprefix("profiles:").split("|"))
            if not all(profiles):
                raise DataError(f"{path.name}:{number} 빈 profile 이름이 있다")
        elif text.startswith("ask:"):
            subject, sep, expected = text.removeprefix("ask:").partition("=")
            if not sep or not subject.strip() or not expected.strip():
                raise DataError(f"{path.name}:{number} 형식은 'ask: 새 관찰 = 기대' 다: {text!r}")
            asks.append(Ask(subject.strip(), expected.strip()))
        else:
            raise DataError(f"{path.name}:{number} 알 수 없는 줄: {text!r}")
    close(number)
    return scenarios


def subjects(pairs: list[Pair]) -> set[str]:
    return {s for pair in pairs for s in (pair.a, pair.b)}
