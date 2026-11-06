"""*_doc 시드 공통 칸 — Food · Growth · Activity 문서 행이 같이 쓰는 칸의 검사 (RAG_plan §1).

`reference.py` 는 안전 상수표(알레르기 · 위험 용어)를 `Term` 으로 바꾸는 로더라 따로 둔다.
YAML 읽기는 같은 `load_reference` 를 쓴다. 도메인 칸은 각 Agent 의 시드 로더가 검사한다
(Activity 는 `activity/doc_seed.py`).

모양이 틀린 행이 하나라도 있으면 읽을 때 `ValueError` 다 — 출처 · 월령 · 검수 칸이 조용히 비면
행 단위로 추적하거나 뺄 수 없다.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal, get_args
from urllib.parse import urlsplit
from uuid import NAMESPACE_URL, UUID, uuid5

from app.agents.common.reference import age_gates

LicenseBasis = Literal[
    "public_law",
    "kogl_1",
    "kogl_2",
    "kogl_3",
    "kogl_4",
    "fact_rewrite",
    "permission",
    "cc_by",  # CC BY 4.0 — 뉴질랜드 보건부. 원문 제목 · 변경 표시 · 라이선스 주소를 같이 낸다
    "ogl_3",  # Open Government Licence v3.0 — 영국 정부 자료. 필수 영문 문장 · 주소를 같이 낸다
    "pdl_1",  # PDL 1.0 (일본 공공데이터 이용규약). 값만 있고 행은 없다 (#510 Part F)
    "cc0",  # 값만 있고 행은 없다
]
DocStatus = Literal["draft", "approved", "retired"]

_LICENSE_BASES: frozenset[str] = frozenset(get_args(LicenseBasis))
_DOC_STATUSES: frozenset[str] = frozenset({"draft", "approved", "retired"})
# 문서 행 월령의 끝. max_month 는 "미만" 이라 이 값까지 쓸 수 있다 — v1 범위 상한
# (reference/age_gates.yaml)
DOC_MONTH_LIMIT = age_gates().v1_month_limit

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


# ── 공개용 출처 (#510 Part C) ────────────────────────────────────────────────
# YAML 의 source_* 는 실제 원문 기관 · 제목 · 주소다 — 행을 되짚어 검수하려고 그대로 둔다.
# Agent 입력(FoodDocRow)과 05 응답(DocCard)에 나가는 값은 아래 함수가 한 곳에서 만든다.
# 한쪽만 거치면 어디서는 NHS 이름이 새고 어디서는 라이선스 표시가 빠진다.

# OGL v3.0 이 요구하는 영문 문장. 한 글자도 바꾸지 않는다 (공백 · 마침표 포함)
OGL_STATEMENT = (
    "Contains public sector information licensed under the Open Government Licence v3.0."
)
OGL_URL = "https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/"
CC_BY_URL = "https://creativecommons.org/licenses/by/4.0/"
# NHS 행의 공개 기관명. NHS 약관 3.6(b) — 번역 · 각색한 콘텐츠는 표준 OGL 문장만 쓰고 NHS 를
# 출처로 밝히지 않는다 (확인일 2026-11-02). 번역은 약관상 각색이라 NHS 행에는 늘 적용된다
NHS_PUBLIC_ORG = "영국 정부 공공 정보"

_CC_BY_NOTICE = f"번역·재구성함 · CC BY 4.0 ({CC_BY_URL})"
_OGL_NOTICE = f"{OGL_STATEMENT} ({OGL_URL})"
# 영문 낱말 안의 nhs(Inhsley)는 NHS 가 아니다. 한글은 조사가 붙으니("NHS에") 영문자만 경계로 본다
_NHS_NAME = re.compile(r"(?<![A-Za-z])NHS(?![A-Za-z])", re.IGNORECASE)


@dataclass(frozen=True)
class PublicDocSource:
    """화면과 모델 입력에 나가는 출처 3칸. `source_locator` 는 내부 추적용이라 여기 없다."""

    source_org: str
    source_title: str
    source_url: str | None


def public_doc_source(meta: DocMeta) -> PublicDocSource:
    """문서 행의 출처를 라이선스가 요구하는 표시에 맞춰 공개용으로 바꾼다.

    - `cc_by` — 원래 기관 · 제목 뒤에 변경 표시와 CC BY 주소를 붙인다. 원문 링크는 `source_url`
      칸으로 나간다.
    - `ogl_3` — 원래 기관 · 제목 · 주소 뒤에 필수 영문 문장과 OGL 주소를 붙인다.
    - `ogl_3` 인 NHS 자료 — 기관 · 제목 · 주소를 OGL 안내로 바꾼다 (`NHS_PUBLIC_ORG` 의 이유).
    - 그 밖의 값은 그대로다. `pdl_1` · `cc0` 은 행이 없어서 표시 문구를 지어내지 않는다 —
      행을 들이는 PR 이 원문 정책과 함께 정한다.
    """
    if meta.license_basis == "cc_by":
        return PublicDocSource(
            meta.source_org, f"{meta.source_title} · {_CC_BY_NOTICE}", meta.source_url
        )
    if meta.license_basis == "ogl_3":
        if _is_nhs(meta):
            return PublicDocSource(NHS_PUBLIC_ORG, _OGL_NOTICE, OGL_URL)
        return PublicDocSource(
            meta.source_org, f"{meta.source_title} · {_OGL_NOTICE}", meta.source_url
        )
    return PublicDocSource(meta.source_org, meta.source_title, meta.source_url)


def _is_nhs(meta: DocMeta) -> bool:
    """원문 주소가 nhs.uk(하위 도메인 포함)이거나, 주소를 못 믿을 때 기관 · 제목에 NHS 가 있는 자료.

    NHS 이름이 새는 쪽이 약관 위반이라 확실하지 않으면 가리는 쪽으로 닫는다.
    """
    host = (urlsplit(meta.source_url or "").hostname or "").lower()
    if host == "nhs.uk" or host.endswith(".nhs.uk"):
        return True
    return bool(_NHS_NAME.search(f"{meta.source_org} {meta.source_title}"))


def doc_row_id(kind: str, doc_key: str) -> UUID:
    """`doc_key` 에서 늘 같게 만드는 문서 행 id (DB 의 uuidv7 과는 다르다).

    포트가 돌려주는 행(`to_row` · `_seed_row`)과 05 의 문서 조회(`entrypoint.describe_docs`)가 같은
    식을 불러 쓴다 — 한쪽만 바뀌면 근거가 조용히 사라진다. `kind` 는 근거 종류(`activity_doc` ·
    `food_doc`)다.
    """
    return uuid5(NAMESPACE_URL, f"{kind}:{doc_key}")


def check_unique_doc_keys(rows: Sequence[DocMeta], *, file: str) -> None:
    seen: set[str] = set()
    for row in rows:
        if row.doc_key in seen:
            raise ValueError(f"{file} 에 doc_key 가 겹친다: {row.doc_key!r}")
        seen.add(row.doc_key)
