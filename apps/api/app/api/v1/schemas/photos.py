import datetime as dt
import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

PhotoLane = Literal["document", "activity"]


class PhotoUploadResponse(BaseModel):
    run_id: str = Field(description="`GET /runs/{rid}/events` 로 읽는 과정을 받는다")


class PhotoUploadTicket(BaseModel):
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


class PhotoEntryDetail(BaseModel):
    """항목에 딸린 정보 한 줄. 🚨 이름이 문서마다 달라서 코드로 가르지 않는다."""

    name: str
    value: str


class PhotoEntry(BaseModel):
    """사진에서 읽어낸 항목 하나. 값은 화면에서 보호자가 고친 뒤의 것이다.

    `date` 아래 칸은 서버가 읽어 보내는 값이다. 저장할 때 다시 보내도 쓰지 않는다. 고칠 수 있는
    값은 제목 · 날짜 · 준비물뿐이고, 나머지는 서버가 run 에 든 값으로 저장한다. `event_id` 만
    화면이 채워 보내는 값이다.
    """

    model_config = ConfigDict(extra="ignore")

    id: str
    kind: Literal["event", "supply", "meal", "todo", "info"]
    title: str = Field(max_length=500)
    date: dt.date | None = Field(default=None, description="못 읽었으면 null. 서버가 채우지 않는다")
    all_day: bool = False
    items: list[str] = Field(default_factory=list, max_length=100)
    needs_review: bool = False
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


class PhotoCommitRequest(BaseModel):
    lane: PhotoLane
    entries: list[PhotoEntry] = Field(
        default_factory=list, description="보호자가 확인한 항목만 온다"
    )
    selected_tags: list[str] = Field(default_factory=list)
    attach_to_calendar: bool = False


class PhotoCommitResponse(BaseModel):
    """🚨 일정은 여기서 만들어지지 않는다. 캘린더에 쓰는 문은 `POST /children/{cid}/events` 다."""

    observations: list[dict] = Field(default_factory=list)
    calendar_date: str | None = None
