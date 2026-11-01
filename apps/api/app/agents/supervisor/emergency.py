"""응급 사전검사 — 보호자 발화에 응급 신호가 있으면 Supervisor 보다 먼저 안내를 낸다.

사전(`reference/emergency_signs.yaml`)을 읽어 규칙(`app/rules/emergency.py`)에 넘기는 로더다. YAML
파싱은 표준 라이브러리가 아니라서 규칙이 아니라 여기에 둔다. 판정은 전부 코드가 한다 — 모델에게
묻지 않는다(루트 §2). 안내 문구는 하나다. Health 의 `emergency` readout 과 글자 단위로
같아야 한다(테스트가 본다).

사전은 검수 전(`medical_reviewed: false`)이어도 켠다. 값이 빠지거나 엉뚱하면 `ValueError` 다 —
기본값으로 채우면 응급 판정이 조용히 바뀐다. 월령 · 체온 기준은 `infant_fever.yaml` 에서만 읽는다.
"""

import logging
from collections.abc import Mapping
from functools import cache
from typing import Any

from app.agents.common.reference import load_reference
from app.agents.health.reference import infant_fever_rule
from app.agents.supervisor.routing import Guidance
from app.rules.emergency import InfantThreshold, Sign, detect
from app.rules.term_match import Term, normalize

logger = logging.getLogger(__name__)

EMERGENCY_CODE = "emergency"
EMERGENCY_MESSAGE = "지금 바로 119에 연락하거나 가까운 응급실로 가세요."

# 명세 §9 의 순서. 이것이 "규칙" 쪽 목록이다 — 사전의 키 집합이 이것과 같아야 로드된다
SIGN_KEYS: tuple[str, ...] = (
    "breathing",
    "swelling",
    "seizure",
    "infant_fever",
    "head_injury",
    "dehydration",
)
_INFANT_KEY = "infant_fever"

_FILE = "emergency_signs.yaml"
_META_KEYS = frozenset({"source", "version", "fetched_at"})
_ROW_COLUMNS = frozenset(
    {
        "key",
        "label",
        "all_of",
        "infant",
        "medical_reviewed",
        "reviewed_by",
        "reviewed_at",
        "sources",
    }
)
_GROUP_COLUMNS = frozenset({"aliases", "guards"})


@cache
def emergency_signs() -> tuple[Sign, ...]:
    return parse_emergency_signs(load_reference(_FILE))


def parse_emergency_signs(data: Mapping[str, Any]) -> tuple[Sign, ...]:
    """사전을 규칙의 `Sign` 으로. 어긋나면 `ValueError` — 기본값으로 채우지 않는다."""
    if extra := set(data) - _META_KEYS - {"signs"}:
        raise ValueError(f"{_FILE} 에 정해지지 않은 키가 있다: {sorted(extra)}")
    rows = data.get("signs")
    if not isinstance(rows, list):
        raise ValueError(f"{_FILE} 의 signs 는 목록이어야 한다")
    keys = [row.get("key") if isinstance(row, Mapping) else None for row in rows]
    if sorted(map(str, keys)) != sorted(SIGN_KEYS):
        raise ValueError(
            f"{_FILE} 의 신호 키가 규칙과 다르다: 사전={sorted(map(str, keys))} "
            f"규칙={sorted(SIGN_KEYS)}"
        )
    return tuple(_parse_row(row) for row in rows)


def _parse_row(row: Mapping[str, Any]) -> Sign:
    key = row["key"] if "key" in row else None
    if set(row) != _ROW_COLUMNS:
        raise ValueError(
            f"{_FILE} 의 {key} 행 칸이 다르다: 빠짐={sorted(_ROW_COLUMNS - set(row))} "
            f"남음={sorted(set(row) - _ROW_COLUMNS)}"
        )
    where = f"{_FILE} 의 {key}"
    infant = row["infant"]
    if not isinstance(infant, bool):
        raise ValueError(f"{where} 의 infant 는 true · false 여야 한다")
    if infant != (key == _INFANT_KEY):
        # 다른 행에 켜면 월령 조건이 엉뚱한 신호에 걸리고, 발열 행에서 꺼지면 모든 아이에게 걸린다
        raise ValueError(f"{where} 의 infant 는 {_INFANT_KEY} 행만 true 여야 한다")
    reviewed = row["medical_reviewed"]
    if not isinstance(reviewed, bool):
        raise ValueError(f"{where} 의 medical_reviewed 는 true · false 여야 한다")
    if reviewed and not str(row["reviewed_by"] or "").strip():
        raise ValueError(f"{where} 은 검수했다고 적었는데 reviewed_by 가 없다")
    if not row["sources"]:
        raise ValueError(f"{where} 에 sources 가 없다 — 출처 없는 응급 표현은 켜지 않는다")
    groups = row["all_of"]
    if not isinstance(groups, list) or not groups:
        raise ValueError(f"{where} 의 all_of 가 비어 있다 — 모든 말에 걸린다")
    terms = tuple(_parse_group(where, index, group) for index, group in enumerate(groups))
    return Sign(key=str(key), all_of=terms, infant=infant)


def _parse_group(where: str, index: int, group: Any) -> Term:
    if not isinstance(group, Mapping) or set(group) != _GROUP_COLUMNS:
        raise ValueError(f"{where} 의 all_of[{index}] 는 aliases · guards 두 칸이어야 한다")
    aliases, guards = group["aliases"], group["guards"]
    if (
        not isinstance(aliases, list)
        or not aliases
        or not all(isinstance(a, str) and normalize(a) for a in aliases)
    ):
        raise ValueError(f"{where} 의 all_of[{index}] aliases 가 비었거나 빈 표현이 있다")
    if not isinstance(guards, list) or not all(isinstance(g, str) and normalize(g) for g in guards):
        raise ValueError(f"{where} 의 all_of[{index}] guards 가 목록이 아니거나 빈 표현이 있다")
    names = [normalize(alias) for alias in aliases]
    for guard in guards:
        covered = normalize(guard)
        if covered in names:
            # 별칭과 같으면 그 별칭이 나온 자리를 전부 덮어 별칭이 통째로 꺼진다
            raise ValueError(f"{where} 의 guard {guard!r} 가 별칭과 같다 — 그 별칭이 꺼진다")
        if not any(name in covered for name in names):
            raise ValueError(f"{where} 의 guard {guard!r} 가 자기 별칭을 덮지 않는다")
    return Term(key=f"{index}", aliases=tuple(aliases), guards=tuple(guards))


def matched_signs(raw_text: str) -> tuple[str, ...]:
    """걸린 신호 키를 사전 순서로. 월령 · 체온 기준은 `infant_fever.yaml` 이 정한다."""
    rule = infant_fever_rule()
    threshold = InfantThreshold(
        below_month=rule.below_month, fever_temperature=rule.fever_temperature
    )
    return detect(raw_text, emergency_signs(), threshold)


def emergency_guidance(raw_text: str) -> Guidance | None:
    """응급 신호가 있으면 안내 한 건, 없으면 None. 로그에는 걸린 신호 키만 싣는다(원문 없음)."""
    keys = matched_signs(raw_text)
    if not keys:
        return None
    logger.info("emergency precheck keys=%s", list(keys))
    return Guidance(code=EMERGENCY_CODE, message=EMERGENCY_MESSAGE)
