import datetime as dt
import uuid
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import ConfigDict, Field, field_validator

from app.api.schema_base import ApiSchema
from app.core.event_draft import MAX_ITEM_NAME

PhotoLane = Literal["document", "activity"]

# 한 번에 저장하는 항목 수. 한 달치 식단표(끼니 셋 × 31일)에 공지 항목을 더해도 넉넉하다
MAX_COMMIT_ENTRIES = 200
# 보호자가 고친 날짜가 이 범위를 벗어나면 받지 않는다. 연간 계획 · 지난 공지를 다 담는다
DATE_WINDOW_YEARS = 3
_KST = ZoneInfo("Asia/Seoul")


class PhotoUploadResponse(ApiSchema):
    run_id: str = Field(description="`GET /runs/{rid}/events` 로 읽는 과정을 받는다")


class PhotoUploadTicket(ApiSchema):
    """사진을 어디로 올릴지. 서버가 사진 본문을 받지 않도록 화면이 S3 에 바로 올린다.

    `s3` 면 `url` 에 `fields` 를 폼 값으로 담고 **맨 마지막에** `file` 을 붙여 POST 한다.
    인증 헤더는 붙이지 않는다(S3 가 서명으로 확인한다). 그다음 `POST .../photos` 에 파일 대신
    `upload_key` 로 `key` 를 보낸다. `direct` 면 지금처럼 `POST .../photos` 에 `file` 을 바로
    보낸다 (로컬 개발).
    """

    mode: Literal["s3", "direct"]
    url: str | None = Field(default=None, description="S3 업로드 주소. 5분 동안만 쓸 수 있다")
    fields: dict[str, str] = Field(default_factory=dict, description="폼에 그대로 담을 값")
    key: str | None = Field(default=None, description="올린 뒤 `upload_key` 로 보낼 값")
    expires_in: int | None = Field(default=None, description="업로드 주소가 유효한 초")


class PhotoEntryDetail(ApiSchema):
    """항목에 딸린 정보 한 줄. 🚨 이름이 문서마다 달라서 코드로 가르지 않는다."""

    name: str
    value: str


class PhotoEntry(ApiSchema):
    """사진에서 읽어낸 항목 하나. 값은 화면에서 보호자가 고친 뒤의 것이다.

    `date` 아래 칸은 서버가 읽어 보내는 값이다. 저장할 때 다시 보내도 쓰지 않는다. 고칠 수 있는
    값은 제목 · 날짜 · 준비물과 **시작 일시**(`starts_at` · `ends_at` · `all_day`)이고, 나머지는
    서버가 run 에 든 값으로 저장한다. `event_id` 만 화면이 채워 보내는 값이다.

    시작 일시는 `date` 가 시작일인 항목에서만 받는다 (마감만 읽은 항목은 마감 날짜만 고친다).
    `starts_at` 의 한국 날짜가 `date` 와 같아야 받고, 다르면 그날 하루 종일로 저장한다.
    `ends_at` 은 `starts_at` 보다 뒤일 때만 함께 저장한다.
    """

    model_config = ConfigDict(extra="ignore")

    id: str
    kind: Literal["event", "supply", "meal", "todo", "info"]
    title: str = Field(max_length=500)
    date: dt.date | None = Field(default=None, description="못 읽었으면 null. 서버가 채우지 않는다")
    all_day: bool = False
    items: list[str] = Field(default_factory=list, max_length=100)
    needs_review: bool = False
    past: bool = Field(
        default=False,
        description=(
            "지난 항목인가. 견줄 날이 모두 사진을 받은 날(한국 날짜)보다 앞이면 참이다. "
            "일정은 시작 · 끝(시작이 없으면 마감), 할 일 · 안내는 시작 · 끝 · 마감 중 "
            "가장 늦은 날과 견준다. 날짜를 못 읽었으면 거짓. "
            "확인 화면 전용이고 저장할 때 다시 보내도 쓰지 않는다"
        ),
    )
    review_reason: str | None = None
    label: str | None = Field(default=None, description='내용 이름. "회신" · "체험비"')
    target_scope: Literal["child", "class", "all", "conditional", "unknown"] | None = Field(
        default=None, description="누구의 일인가. `conditional` 은 해당하는 아이만"
    )
    starts_at: dt.datetime | None = Field(default=None, description="시작 일시. 못 읽었으면 null")
    ends_at: dt.datetime | None = Field(default=None, description="끝 일시. 없으면 null")
    due_at: dt.datetime | None = Field(default=None, description="마감. `date` 와 다를 수 있다")
    details: list[PhotoEntryDetail] = Field(
        default_factory=list, description="금액 · 장소 · 회신 수단처럼 줄로 보여 주는 값"
    )
    source_quote: str | None = Field(default=None, description="근거 문장. 이름은 가린 글자다")
    event_id: uuid.UUID | None = Field(
        default=None,
        description=(
            "이 항목으로 넣은 일정. `POST /children/{cid}/events` 응답의 `event.id` 를 저장할 때 "
            "보낸다. 서버가 그 일정에 이 항목을 출처로 단다. 일정을 안 넣었으면 비운다"
        ),
    )

    @field_validator("title")
    @classmethod
    def _title_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("제목이 비어 있어요")
        return value.strip()

    @field_validator("items")
    @classmethod
    def _items_are_names(cls, value: list[str]) -> list[str]:
        names = [name.strip() for name in value]
        if any(not name or len(name) > MAX_ITEM_NAME for name in names):
            raise ValueError(f"준비물은 1~{MAX_ITEM_NAME}자예요")
        return names

    @field_validator("date")
    @classmethod
    def _date_in_window(cls, value: dt.date | None) -> dt.date | None:
        # 0001-01-01 은 시간대를 붙이다 OverflowError 로 500 이 났다 (#726 리뷰)
        if value is None:
            return value
        today = dt.datetime.now(_KST).date()
        if abs(value.year - today.year) > DATE_WINDOW_YEARS:
            raise ValueError(f"날짜는 앞뒤 {DATE_WINDOW_YEARS}년 안이어야 해요")
        return value


class PhotoCommitRequest(ApiSchema):
    lane: PhotoLane
    entries: list[PhotoEntry] = Field(
        default_factory=list,
        max_length=MAX_COMMIT_ENTRIES,
        description="보호자가 확인한 항목만 온다. 같은 id 를 두 번 보내면 422",
    )
    selected_tags: list[str] = Field(default_factory=list)
    attach_to_calendar: bool = False


class PhotoCommitResponse(ApiSchema):
    """🚨 일정은 여기서 만들어지지 않는다. 캘린더에 쓰는 문은 `POST /children/{cid}/events` 다."""

    observations: list[dict] = Field(default_factory=list)
    calendar_date: str | None = None
