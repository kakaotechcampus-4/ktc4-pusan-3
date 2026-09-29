"""동의 종류(scope)마다 정해지는 규칙 — GET /policies 가 본문과 함께 내려준다 (#91).

required · sensitive 는 서버가 지키는 **규칙**이라 여기(코드)에 둔다. 가입 검사가 이 값과 같은
기준으로 막아야 하고, 규칙은 옛 버전이 아니라 지금 값이 맞다. 프론트에도 같은 값을 적어두면
두 곳이 어긋나므로 여기 한 곳에만 두고 서버가 채워 준다.

제목(label) · 법적 근거(legal_basis)는 여기 없다 — 보호자가 **본 글**이라 본문처럼 버전마다
`policy_version` 에 저장한다 (#168 리뷰). 코드에 두면 제목을 바꾸는 순간 옛 버전에 동의한
기록에도 새 제목이 소급 적용된다.

⚠️ 동의 화면(`apps/web/src/lib/consent.ts` CONSENT_ITEMS)에도 같은 값이 아직 적혀 있다. 화면이 이
   응답을 그리게 되면(#90) 그쪽 상수를 지운다 — 그때부터 제목 · 근거의 출처는 policy_version 하나다.

🚨 순서가 화면 순서다. 계정 동의 → 아이 동의, 민감정보(child_health)는 마지막이다.
   위치(location)는 선택이지만 보호자 본인의 동의라 계정 동의 무리의 끝에 둔다.
🚨 여기 없는 scope 는 약관이 등록돼 있어도 응답에 나가지 않는다. 동의를 받지 않는
   scope(`quality_improve`)가 화면에 새어 나가지 않게 하려는 것이다.
"""

from dataclasses import dataclass

from app.domains.consent.models import ConsentScope


@dataclass(frozen=True)
class ScopeInfo:
    required: bool
    """없으면 서비스가 성립하지 않는가. 제출을 막는 것은 이 값뿐이다."""
    sensitive: bool
    """민감정보라 다른 동의와 구분해서 받아야 하는가 (개인정보 보호법 제23조)."""


SCOPE_CATALOG: dict[ConsentScope, ScopeInfo] = {
    ConsentScope.SERVICE_TERMS: ScopeInfo(required=True, sensitive=False),
    ConsentScope.PRIVACY_ACCOUNT: ScopeInfo(required=True, sensitive=False),
    # 🚨 선택이다. 필수로 받으면 선택이어야 할 동의를 강제하는 것이 된다 (#172).
    ConsentScope.LOCATION: ScopeInfo(required=False, sensitive=False),
    ConsentScope.CHILD_BASIC: ScopeInfo(required=True, sensitive=False),
    ConsentScope.CHILD_HEALTH: ScopeInfo(required=True, sensitive=True),
}
"""dict 는 넣은 순서를 지킨다 — 이 순서가 응답 순서다."""
