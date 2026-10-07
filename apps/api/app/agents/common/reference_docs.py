"""*_doc 시드 공통 칸 — Food · Growth · Activity 문서 행이 같이 쓰는 칸의 검사 (RAG_plan §1).

`reference.py` 는 안전 상수표(알레르기 · 위험 용어)를 `Term` 으로 바꾸는 로더라 따로 둔다.
YAML 읽기는 같은 `load_reference` 를 쓴다. 도메인 칸은 각 Agent 의 시드 로더가 검사한다
(Activity 는 `activity/doc_seed.py`).

모양이 틀린 행이 하나라도 있으면 읽을 때 `ValueError` 다 — 출처 · 월령 · 검수 칸이 조용히 비면
행 단위로 추적하거나 뺄 수 없다.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal

from app.rules.age import V1_MONTH_LIMIT

LicenseBasis = Literal[
    "public_law", "kogl_1", "kogl_2", "kogl_3", "kogl_4", "fact_rewrite", "permission"
]
DocStatus = Literal["draft", "approved", "retired"]

_LICENSE_BASES: frozenset[str] = frozenset(
    {"public_law", "kogl_1", "kogl_2", "kogl_3", "kogl_4", "fact_rewrite", "permission"}
)
_DOC_STATUSES: frozenset[str] = frozenset({"draft", "approved", "retired"})
# 문서 행 월령의 끝. max_month 는 "미만" 이라 이 값까지 쓸 수 있다 — v1 범위 상한 (app/rules/age.py)
DOC_MONTH_LIMIT = V1_MONTH_LIMIT

DOC_COMMON_KEYS: frozenset[str] = frozenset(
    {
        "doc_key",
        "row_type",
        "title",
        "body",
        "min_month",
        "max_month",
        "tags",
        "source_title",
        "source_org",
        "source_year",
        "source_locator",
        "source_url",
        "license_basis",
        "status",
        "authored_by",
        "reviewed_by",
        "reviewed_at",
        "version",
    }
)
_DOC_REQUIRED_TEXT = (
    "doc_key",
    "row_type",
    "title",
    "body",
    "source_title",
    "source_org",
    "source_locator",
    "authored_by",
)


@dataclass(frozen=True)
class DocMeta:
    """문서 행의 공통 칸. 월령은 `min_month` 이상 `max_month` **미만**이다."""

    doc_key: str
    row_type: str
    title: str
    body: str  # 우리가 새로 쓴 문장. 원문을 옮기지 않는다
    min_month: int
    max_month: int
    tags: tuple[str, ...]
    source_title: str
    source_org: str
    source_year: int
    source_locator: str
    source_url: str | None
    license_basis: LicenseBasis
    status: DocStatus
    authored_by: str
    reviewed_by: str | None
    reviewed_at: date | None
    version: int

    @property
    def search_text(self) -> str:
        """임베딩 입력. YAML 에 따로 적지 않는다 — 손으로 적으면 본문과 어긋난다 (§1)."""
        return " ".join((self.title, *self.tags))

    def covers(self, months: int) -> bool:
        return self.min_month <= months < self.max_month


def parse_doc_meta(
    raw: Mapping[str, Any],
    *,
    file: str,
    key_prefix: str,
    row_types: frozenset[str],
    domain_keys: frozenset[str],
) -> DocMeta:
    """문서 행 하나의 공통 칸을 검사한다. 틀린 곳이 있으면 `ValueError` — 읽을 때 바로 실패한다."""
    doc_key = raw.get("doc_key")
    where = f"{file} 행 {doc_key!r}"
    if extra := set(raw) - DOC_COMMON_KEYS - domain_keys:
        raise ValueError(f"{where} 에 정해지지 않은 칸이 있다: {sorted(extra)}")
    for key in _DOC_REQUIRED_TEXT:
        value = raw.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{where} 에 {key} 가 없다")
    if not doc_key.startswith(key_prefix):
        raise ValueError(f"{where} 의 doc_key 는 {key_prefix!r} 로 시작해야 한다")
    if raw["row_type"] not in row_types:
        raise ValueError(f"{where} 의 row_type {raw['row_type']!r} 은 정해진 값이 아니다")

    min_month, max_month = raw.get("min_month"), raw.get("max_month")
    for value in (min_month, max_month):
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValueError(f"{where} 의 월령은 정수여야 한다 (max_month 도 비울 수 없다)")
    if not 0 <= min_month < max_month <= DOC_MONTH_LIMIT:
        raise ValueError(
            f"{where} 의 월령은 0 ≤ min({min_month}) < max({max_month}) ≤ {DOC_MONTH_LIMIT}"
        )

    year = raw.get("source_year")
    if not isinstance(year, int) or isinstance(year, bool):
        raise ValueError(f"{where} 의 source_year 는 정수여야 한다")
    license_basis = raw.get("license_basis")
    if license_basis not in _LICENSE_BASES:
        raise ValueError(f"{where} 의 license_basis {license_basis!r} 은 정해진 값이 아니다")
    status = raw.get("status")
    if status not in _DOC_STATUSES:
        raise ValueError(f"{where} 의 status {status!r} 는 정해진 값이 아니다")

    # 작성자 · 검수자는 GitHub 아이디로 적는다 (§1). 앞뒤 공백만 다른 같은 사람을 놓치지 않게 지운다
    authored_by = raw["authored_by"].strip()
    reviewed_by = raw.get("reviewed_by")
    if reviewed_by is not None:
        if not isinstance(reviewed_by, str):
            raise ValueError(f"{where} 의 reviewed_by 는 GitHub 아이디(문자열)여야 한다")
        reviewed_by = reviewed_by.strip() or None
    reviewed_at = raw.get("reviewed_at")
    if reviewed_by is not None and reviewed_by == authored_by:
        raise ValueError(f"{where} 는 작성자와 검수자가 같다")
    if status == "approved" and not (reviewed_by and isinstance(reviewed_at, date)):
        raise ValueError(f"{where} 는 approved 인데 검수자 · 검수일이 없다")

    tags = raw.get("tags") or []
    if not isinstance(tags, list) or not all(isinstance(t, str) and t for t in tags):
        raise ValueError(f"{where} 의 tags 는 문자열 목록이어야 한다")
    version = raw.get("version", 1)
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise ValueError(f"{where} 의 version 은 1 이상의 정수여야 한다")

    return DocMeta(
        doc_key=doc_key,
        row_type=raw["row_type"],
        title=raw["title"].strip(),
        body=" ".join(raw["body"].split()),
        min_month=min_month,
        max_month=max_month,
        tags=tuple(tags),
        source_title=raw["source_title"].strip(),
        source_org=raw["source_org"].strip(),
        source_year=year,
        source_locator=raw["source_locator"].strip(),
        source_url=raw.get("source_url"),
        license_basis=license_basis,
        status=status,
        authored_by=authored_by,
        reviewed_by=reviewed_by,
        reviewed_at=reviewed_at,
        version=version,
    )


def check_unique_doc_keys(rows: Sequence[DocMeta], *, file: str) -> None:
    seen: set[str] = set()
    for row in rows:
        if row.doc_key in seen:
            raise ValueError(f"{file} 에 doc_key 가 겹친다: {row.doc_key!r}")
        seen.add(row.doc_key)
