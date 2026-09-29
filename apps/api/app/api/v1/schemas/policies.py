"""GET /policies 응답 — 이슈 #91 · #172."""

from pydantic import BaseModel, ConfigDict

from app.domains.consent.models import ConsentScope


class PolicyResponse(BaseModel):
    """동의 화면이 그리는 약관 한 건.

    🚨 화면은 `version` 을 그대로 가입 요청(`POST /auth/{provider}/signup` 의
       `consents[].policy_version`)에 싣는다. 상수로 들고 있지 않는다.
    🚨 약관 전문은 `html_path` 의 정본 페이지를 **손대지 않고** 띄운다. 원문(마크다운)은 일부러
       내려보내지 않는다 — 화면이 원문을 직접 그리면 문단을 숨기거나 순서를 바꿀 수 있어 "보호자가
       본 글" 을 증명하지 못한다 (멘토 #71 리뷰 4번). 원문은 DB 에 남아 동의 증빙과 해시의
       원본이 된다.
    """

    # 필드 아래 설명 글이 Swagger(API 계약 정본)에 나가게 한다. 이 설정이 없으면 pydantic 은
    # 필드 docstring 을 버린다.
    model_config = ConfigDict(use_attribute_docstrings=True)

    scope: ConsentScope
    version: str
    """가입 요청의 `consents[].policy_version` 에 그대로 싣는 값."""
    label: str
    """법적 표기. 체크박스 라벨이고 전문 페이지의 제목이다."""
    legal_basis: str | None
    """화면에 그대로 보여 주는 근거 조문. 없으면 보여 주지 않는다."""
    required: bool
    """필수 동의인가. 가입 버튼을 막는 것은 이 값뿐이다 (위치는 선택)."""
    sensitive: bool
    """민감정보라 다른 동의와 구분해서 받아야 하는가 (개인정보 보호법 제23조)."""
    html_path: str | None
    """"전문 보기" 가 여는 정본 HTML 페이지의 경로 (#172). 화면은 이 페이지를 손대지 않고 띄운다.

    `/api/v1` 아래 경로다 (예: `/policies/location/draft-1`) — 절대 주소는 API 기준 주소 + 이 값.
    웹뷰나 새 창으로 연다. iframe 은 권하지 않는다 — 앱 껍데기(apps/mobile)가 다른 주소로의
    이동을 가로채 시스템 브라우저로 넘길 수 있다.
    null 이면 정본이 없는 자리 표시 글(draft-0)이다.
    """
