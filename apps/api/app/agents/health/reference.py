"""Health 기준표 두 장 — 영유아 검진 시기 · 국가예방접종 표준접종 시기.

값은 `reference/checkup_nhis.yaml` · `reference/vaccine_2026.yaml` 에 있고 여기는 읽는 쪽이다
(`load_reference` 가 `source` · `version` · `fetched_at` 이 없으면 막는다). 계산은
`tools/checkup.py` 가 한다. 모양이 틀리면 import 가 아니라 첫 호출에서 `ValueError` 다 — 빈 표로
통과시키면 검진 안내가 조용히 사라진다.

접종 표는 해마다 새 파일(`vaccine_<연도>.yaml`)로 갈아 끼운다. 파일 이름은 `_VACCINE_FILE` 한 곳.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from functools import cache
from typing import Any, Literal

from app.agents.common.reference import load_reference

_VACCINE_FILE = "vaccine_2026.yaml"

_META_KEYS = frozenset({"source", "version", "fetched_at"})
_ROUND_KEYS = frozenset({"round", "start", "first", "last"})
_DOSE_KEYS = frozenset({"vaccine", "dose", "first_month", "last_month"})
_GENERAL_COUNT = 8
_ORAL_COUNT = 4
_VACCINE_END_MONTH = 156  # 만 13세. 접종 표는 이 월령 앞(만 12세)에서 끝난다


@dataclass(frozen=True)
class CheckupRound:
    kind: Literal["general", "oral"]
    round: int
    start: Literal["day", "month"]  # 1차(일반)만 day
    first: int  # 14 (일) 또는 4 (개월)
    last: int  # 35 (일) 또는 6 (개월) — 포함


@dataclass(frozen=True)
class CheckupTable:
    rounds: tuple[CheckupRound, ...]
    source: str
    version: str


@dataclass(frozen=True)
class VaccineDose:
    vaccine: str  # 화면 이름 그대로 (예: "DTaP")
    dose: str  # "1차" · "추가" …
    first_month: int
    last_month: int  # 포함


@dataclass(frozen=True)
class VaccineTable:
    doses: tuple[VaccineDose, ...]
    year: int
    source: str


@cache
def checkup_table() -> CheckupTable:
    return parse_checkup_table(load_reference("checkup_nhis.yaml"))


def parse_checkup_table(data: Mapping[str, Any]) -> CheckupTable:
    if extra := set(data) - _META_KEYS - {"general", "oral"}:
        raise ValueError(f"checkup_nhis.yaml 에 정해지지 않은 키가 있다: {sorted(extra)}")
    rounds: list[CheckupRound] = []
    for kind, count in (("general", _GENERAL_COUNT), ("oral", _ORAL_COUNT)):
        raw_rows = data.get(kind) or []
        if len(raw_rows) != count:
            raise ValueError(f"checkup_nhis.yaml {kind} 는 {count}회여야 한다: {len(raw_rows)}회")
        parsed = [_parse_round(kind, raw) for raw in raw_rows]
        if [row.round for row in parsed] != list(range(1, count + 1)):
            raise ValueError(f"checkup_nhis.yaml {kind} 의 차수가 1부터 순서대로가 아니다")
        rounds.extend(parsed)
    return CheckupTable(
        rounds=tuple(rounds), source=str(data["source"]), version=str(data["version"])
    )


def _parse_round(kind: str, raw: Mapping[str, Any]) -> CheckupRound:
    if set(raw) != _ROUND_KEYS:
        raise ValueError(
            f"checkup_nhis.yaml {kind} 의 칸은 {sorted(_ROUND_KEYS)} 여야 한다: {dict(raw)}"
        )
    if raw["start"] not in ("day", "month"):
        raise ValueError(f"checkup_nhis.yaml {kind} {raw['round']}차 단위가 day · month 가 아니다")
    first, last = raw["first"], raw["last"]
    if not (isinstance(first, int) and isinstance(last, int)) or first > last or first < 0:
        raise ValueError(
            f"checkup_nhis.yaml {kind} {raw['round']}차 기간이 맞지 않는다: {first}~{last}"
        )
    return CheckupRound(
        kind=kind,  # type: ignore[arg-type]  # 위에서 두 값만 돈다
        round=raw["round"],
        start=raw["start"],
        first=first,
        last=last,
    )


@cache
def vaccine_table() -> VaccineTable:
    return parse_vaccine_table(load_reference(_VACCINE_FILE))


def parse_vaccine_table(data: Mapping[str, Any]) -> VaccineTable:
    if extra := set(data) - _META_KEYS - {"year", "doses"}:
        raise ValueError(f"{_VACCINE_FILE} 에 정해지지 않은 키가 있다: {sorted(extra)}")
    year = data.get("year")
    if not isinstance(year, int) or isinstance(year, bool):
        raise ValueError(f"{_VACCINE_FILE} 의 year 는 정수여야 한다: {year!r}")
    raw_doses = data.get("doses") or []
    if not raw_doses:
        raise ValueError(f"{_VACCINE_FILE} 에 접종 칸이 없다")
    doses: list[VaccineDose] = []
    seen: set[tuple[str, str]] = set()
    for raw in raw_doses:
        if set(raw) != _DOSE_KEYS:
            raise ValueError(f"{_VACCINE_FILE} 의 칸은 {sorted(_DOSE_KEYS)} 여야 한다: {dict(raw)}")
        first, last = raw["first_month"], raw["last_month"]
        if (
            not (isinstance(first, int) and isinstance(last, int))
            or not 0 <= first <= last < _VACCINE_END_MONTH
        ):
            raise ValueError(
                f"{_VACCINE_FILE} {raw['vaccine']} {raw['dose']} 기간이 "
                f"0~{_VACCINE_END_MONTH - 1}개월 안에 있지 않다: {first}~{last}"
            )
        key = (raw["vaccine"], raw["dose"])
        if key in seen:
            raise ValueError(f"{_VACCINE_FILE} 에 같은 접종 칸이 겹친다: {key}")
        seen.add(key)
        doses.append(VaccineDose(raw["vaccine"], raw["dose"], first, last))
    return VaccineTable(doses=tuple(doses), year=year, source=str(data["source"]))
