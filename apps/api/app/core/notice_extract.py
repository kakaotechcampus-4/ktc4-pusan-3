"""공지 한 건을 읽은 결과의 계약. 읽는 쪽(providers)과 저장하는 쪽(domains)이 같이 본다.

`core/event_draft.py` 와 같은 자리다 — providers 는 domains 를 import 하지 못한다.

🚨 모델은 날짜를 계산하지 않는다. `date_text` · `due_text` · `issued_text` 는 원문 표현을 그대로
   옮긴 글자이고, 확정 날짜는 `app/rules/notice_dates.py` 가 만든다 (CLAUDE.md §2 · §3).
🚨 건강 안내는 기관이 보낸 글이지 아이의 기록이 아니다. 이 계약에는 아이 건강 칸이 없다.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict

# domains/notice/models.py 의 enum 과 같은 값. core 는 domains 를 import 하지 않아 다시 적는다
DocKindValue = Literal["daily_note", "newsletter", "meal_plan", "schedule", "message", "other"]
AudienceValue = Literal["child", "class", "institution", "unknown"]
HandlingValue = Literal["calendar", "todo", "info"]
TargetValue = Literal["child", "class", "all", "conditional", "unknown"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ItemDetail(_Strict):
    """항목에 딸린 나머지 정보 한 줄. 코드가 읽지 않고 화면에 그대로 보여 준다.

    문서마다 나오는 정보가 달라서 칸을 미리 정하지 않는다 — 반별 금액 · 장소 · 제출 서류.
    🚨 이름이 문서마다 제각각이다. 이 값으로 코드를 가르지 않는다.
    """

    name: str
    value: str


class ExtractedItem(_Strict):
    handling: HandlingValue
    label: str
    title: str
    target_scope: TargetValue
    date_text: str | None
    due_text: str | None
    details: list[ItemDetail]
    supplies: list[str]
    source_quote: str


class NoticeExtract(_Strict):
    doc_kind: DocKindValue
    audience_scope: AudienceValue
    title: str | None
    issued_text: str | None
    base_month_text: str | None = None
    is_correction: bool
    person_names: list[str]
    institution_names: list[str]
    items: list[ExtractedItem]


class ImageNoticeExtract(NoticeExtract):
    """사진은 글자가 없어 모델이 옮겨 적은 글자를 같이 받는다."""

    transcript: str
