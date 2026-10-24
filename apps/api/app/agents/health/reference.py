"""Health 기준표 — 검진 · 접종 시기, 증상 · 질환 사전, (질환 × 증상) 안내.

값은 `reference/checkup_nhis.yaml` · `reference/vaccine_2026.yaml` 에 있고 여기는 읽는 쪽이다
(`load_reference` 가 `source` · `version` · `fetched_at` 이 없으면 막는다). 계산은
`tools/checkup.py` 가 한다. 모양이 틀리면 import 가 아니라 첫 호출에서 `ValueError` 다 — 빈 표로
통과시키면 검진 안내가 조용히 사라진다.

접종 표는 해마다 새 파일(`vaccine_<연도>.yaml`)로 갈아 끼운다. 파일 이름은 `_VACCINE_FILE` 한 곳.

증상 · 질환 사전은 `hazard_terms.yaml` 의 terms 와 같은 모양이고 매칭은
`app/rules/term_match.py` 다. (질환 × 증상) 안내는 `medical_reviewed: true` 인 행만 로드한다 —
검수 전 행도 모양은 검사하고 결과에서 뺀다.

3개월 미만 발열 응급 기준(`infant_fever.yaml`)은 검수 전이어도 로드한다 — 끄면 응급 안내가 사라진다.
검수 상태는 결과에 같이 싣는다(`InfantFeverRule.medical_reviewed`).
"""

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from functools import cache
from typing import Any, Literal

from app.agents.common.reference import load_reference
from app.rules.term_match import Term, normalize

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


# ── 증상 · 질환 사전 ──

_TERM_KEYS = frozenset({"code", "label", "aliases", "guards"})
_MIN_ALIAS_LENGTH = 2  # 한 글자 별칭("열")은 아무 글에나 걸린다


@dataclass(frozen=True)
class CodeBook:
    """증상 · 질환 사전 공용. `terms` 의 `Term.key` 는 코드다."""

    terms: tuple[Term, ...]
    labels: Mapping[str, str]  # 코드 → 화면 이름 ("cough" → "기침")


@cache
def symptom_codes() -> CodeBook:
    return parse_code_book(load_reference("symptom_codes.yaml"), section="symptom_codes.yaml")


@cache
def condition_codes() -> CodeBook:
    return parse_code_book(load_reference("condition_codes.yaml"), section="condition_codes.yaml")


def parse_code_book(data: Mapping[str, Any], *, section: str) -> CodeBook:
    """사전 yaml 을 읽은 dict 를 검사해 `CodeBook` 으로. `section` 은 오류 문구에 쓰는 파일 이름."""
    terms: list[Term] = []
    labels: dict[str, str] = {}
    alias_owner: dict[str, str] = {}
    for raw in data.get("terms") or []:
        code = raw.get("code")
        if extra := set(raw) - _TERM_KEYS:
            raise ValueError(f"{section} 코드 {code!r} 에 정해지지 않은 칸이 있다: {sorted(extra)}")
        label = raw.get("label")
        aliases = tuple(raw.get("aliases") or ())
        if not code or not label:
            raise ValueError(f"{section} 에 code · label 이 없는 줄이 있다: {dict(raw)}")
        if label not in aliases:
            raise ValueError(f"{section} 코드 {code!r} 의 aliases 에 label 자신이 없다")
        if code in labels:
            raise ValueError(f"{section} 에 코드가 겹친다: {code!r}")
        for alias in aliases:
            key = normalize(alias)
            if len(key) < _MIN_ALIAS_LENGTH:
                raise ValueError(f"{section} 코드 {code!r} 에 한 글자 별칭 {alias!r} — 과차단")
            if alias_owner.get(key, code) != code:
                raise ValueError(
                    f"{section} 에 별칭이 겹친다: {alias!r} ({alias_owner[key]} · {code})"
                )
            alias_owner[key] = code
        terms.append(Term(key=code, aliases=aliases, guards=tuple(raw.get("guards") or ())))
        labels[code] = label
    return CodeBook(terms=tuple(terms), labels=labels)


# ── (질환 × 증상) 안내 ──

_GUIDANCE_FILE = "condition_guidance.yaml"
_GUIDANCE_KEYS = frozenset(
    {
        "condition",
        "symptom",
        "text",
        "source_title",
        "publisher",
        "url",
        "license",
        "medical_reviewed",
        "reviewed_by",
        "reviewed_at",
    }
)
_GUIDANCE_REQUIRED = ("condition", "symptom", "text", "source_title", "publisher", "url", "license")
# 문구를 그대로 옮겨 싣는 쓰임에 맞는 이용 조건만. 상업적이용금지 · 변경금지(KOGL-3 · 4)는 뺀다
ALLOWED_LICENSES = frozenset({"KOGL-0", "KOGL-1", "고시", "CC-BY"})
# 원인 · 병명을 짐작하는 말 (명세 §3 lookup_condition_guidance 금지). 문구에만 건다
_GUESS_PHRASES = ("로 인해", "에 걸린")  # eval 공통 금지어(judges.FORBIDDEN)와 같은 범위


@dataclass(frozen=True)
class Guidance:
    condition: str  # 질환 코드
    symptom: str  # 증상 코드
    text: str
    source_title: str
    publisher: str
    url: str
    license: str  # "KOGL-0" · "KOGL-1" · "고시" · "CC-BY"


@cache
def condition_guidance() -> Mapping[tuple[str, str], Guidance]:
    """`medical_reviewed: true` 인 행만. 검수 전 행은 로드하지 않는다."""
    return parse_condition_guidance(load_reference(_GUIDANCE_FILE))


def parse_condition_guidance(data: Mapping[str, Any]) -> Mapping[tuple[str, str], Guidance]:
    """yaml 을 읽은 dict 를 검사해 (질환 코드, 증상 코드) → 안내로.

    검수 전 행도 모양은 검사한다 — 검수를 요청할 때 처음 깨지지 않게. 결과에는 넣지 않는다.
    """
    if extra := set(data) - _META_KEYS - {"rows"}:
        raise ValueError(f"{_GUIDANCE_FILE} 에 정해지지 않은 키가 있다: {sorted(extra)}")
    conditions = {term.key for term in condition_codes().terms}
    symptoms = {term.key for term in symptom_codes().terms}
    rows: dict[tuple[str, str], Guidance] = {}
    seen: set[tuple[str, str]] = set()
    for raw in data.get("rows") or []:
        where = f"{_GUIDANCE_FILE} {raw.get('condition')}×{raw.get('symptom')}"
        if extra := set(raw) - _GUIDANCE_KEYS:
            raise ValueError(f"{where} 에 정해지지 않은 칸이 있다: {sorted(extra)}")
        if missing := [key for key in _GUIDANCE_REQUIRED if not str(raw.get(key) or "").strip()]:
            raise ValueError(f"{where} 에 빈 칸이 있다: {missing}")
        key = (raw["condition"], raw["symptom"])
        if key[0] not in conditions:
            raise ValueError(f"{where} 의 질환 코드가 사전(condition_codes.yaml)에 없다")
        if key[1] not in symptoms:
            raise ValueError(f"{where} 의 증상 코드가 사전(symptom_codes.yaml)에 없다")
        if raw["license"] not in ALLOWED_LICENSES:
            raise ValueError(
                f"{where} 의 license {raw['license']!r} 는 허용 밖이다: {sorted(ALLOWED_LICENSES)}"
            )
        if phrase := next((p for p in _GUESS_PHRASES if p in raw["text"]), None):
            raise ValueError(f"{where} 의 문구에 원인 · 병명 추정 표현이 있다: {phrase!r}")
        if key in seen:
            raise ValueError(f"{_GUIDANCE_FILE} 에 같은 (질환 × 증상) 행이 겹친다: {key}")
        seen.add(key)
        reviewed = raw.get("medical_reviewed", False)
        if not isinstance(reviewed, bool):
            raise ValueError(f"{where} 의 medical_reviewed 는 true · false 여야 한다: {reviewed!r}")
        if reviewed and not str(raw.get("reviewed_by") or "").strip():
            raise ValueError(f"{where} 은 검수했다고 적었는데 reviewed_by 가 없다")
        if reviewed:
            rows[key] = Guidance(
                condition=key[0],
                symptom=key[1],
                text=raw["text"],
                source_title=raw["source_title"],
                publisher=raw["publisher"],
                url=raw["url"],
                license=raw["license"],
            )
    return rows


_INFANT_FEVER_FILE = "infant_fever.yaml"
_INFANT_FEVER_KEYS = frozenset(
    {
        "below_month",
        "fever_temperature",
        "medical_reviewed",
        "reviewed_by",
        "reviewed_at",
        "sources",
    }
)
_SOURCE_KEYS = frozenset({"title", "publisher", "url", "note"})
_SOURCE_REQUIRED = ("title", "publisher", "url")


@dataclass(frozen=True)
class InfantFeverRule:
    """생후 `below_month` 개월 미만 + `fever_temperature` ℃ 이상(또는 fever 코드) → 응급 안내."""

    below_month: int
    fever_temperature: Decimal
    medical_reviewed: bool  # 검수 전이어도 규칙은 켠다 — 상태를 알 수 있게 싣기만 한다


@cache
def infant_fever_rule() -> InfantFeverRule:
    return parse_infant_fever_rule(load_reference(_INFANT_FEVER_FILE))


def parse_infant_fever_rule(data: Mapping[str, Any]) -> InfantFeverRule:
    """값이 빠지거나 엉뚱하면 `ValueError` — 기본값으로 채우면 응급 안내가 조용히 바뀐다."""
    if extra := set(data) - _META_KEYS - _INFANT_FEVER_KEYS:
        raise ValueError(f"{_INFANT_FEVER_FILE} 에 정해지지 않은 키가 있다: {sorted(extra)}")
    month = data.get("below_month")
    if isinstance(month, bool) or not isinstance(month, int) or not 1 <= month <= 12:
        raise ValueError(f"{_INFANT_FEVER_FILE} 의 below_month 는 1~12 정수여야 한다: {month!r}")
    try:
        temperature: Decimal | None = Decimal(str(data.get("fever_temperature")))
    except InvalidOperation:
        temperature = None
    if temperature is None or not temperature.is_finite() or not 37 <= temperature <= 40:
        raise ValueError(
            f"{_INFANT_FEVER_FILE} 의 fever_temperature 는 37.0~40.0 이어야 한다: "
            f"{data.get('fever_temperature')!r}"
        )
    reviewed = data.get("medical_reviewed")
    if not isinstance(reviewed, bool):
        raise ValueError(f"{_INFANT_FEVER_FILE} 의 medical_reviewed 는 true · false 여야 한다")
    if reviewed and not str(data.get("reviewed_by") or "").strip():
        raise ValueError(f"{_INFANT_FEVER_FILE} 은 검수했다고 적었는데 reviewed_by 가 없다")
    sources = data.get("sources") or []
    if not sources:
        raise ValueError(f"{_INFANT_FEVER_FILE} 에 출처(sources)가 없다")
    for source in sources:
        if extra := set(source) - _SOURCE_KEYS:
            raise ValueError(
                f"{_INFANT_FEVER_FILE} 출처에 정해지지 않은 칸이 있다: {sorted(extra)}"
            )
        if missing := [key for key in _SOURCE_REQUIRED if not str(source.get(key) or "").strip()]:
            raise ValueError(f"{_INFANT_FEVER_FILE} 출처에 빈 칸이 있다: {missing}")
    return InfantFeverRule(
        below_month=month, fever_temperature=temperature, medical_reviewed=reviewed
    )
