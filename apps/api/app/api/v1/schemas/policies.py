"""GET /policies 응답 — 이슈 #91."""

from pydantic import BaseModel

from app.domains.consent.models import ConsentScope


class PolicyResponse(BaseModel):
    """동의 화면이 그리는 약관 한 건.

    🚨 화면은 `version` 을 그대로 가입 요청(`POST /auth/{provider}/signup` 의
       `consents[].policy_version`)에 싣는다. 상수로 들고 있지 않는다.
    🚨 `content` 는 DB 에 저장된 원문 그대로다. 화면은 이것을 변형 없이 그린다 —
       조건부로 문단을 숨기거나 순서를 바꾸면 "보호자가 본 글"과 "동의 기록이 가리키는
       글"이 달라진다 (멘토 #71 리뷰 4번).
    """

    scope: ConsentScope
    version: str
    label: str
    legal_basis: str | None
    required: bool
    sensitive: bool
    content: str
    html_path: str | None
    """"전문 보기" 가 여는 정본 HTML 페이지의 경로 (#172). `/api/v1` 아래 경로라 API 클라이언트의
    다른 경로와 같은 기준이다 — 예: `/policies/location/draft-1`.

    🚨 화면은 이 페이지를 **손대지 않고** 띄운다 (웹뷰 · 새 창 · iframe). 서버가 저장한 완성본을
       그대로 보여 줘야 "저장된 정본 = 보호자가 본 것" 이 된다 (멘토 #71-4).
    null 이면 정본이 없는 자리 표시 글(draft-0)이다.
    """
