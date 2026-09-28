"""동의 종류(scope)마다 정해지는 표시 정보 — GET /policies 가 본문과 함께 내려준다 (#91).

label · legal_basis · required · sensitive 는 버전이 아니라 scope 로 정해지는 값이라
`policy_version` 컬럼으로 두지 않는다. 프론트에도 같은 값을 적어두면 두 곳이 어긋나므로
여기 한 곳에만 두고 서버가 채워 준다.

값은 동의 화면(`apps/web/src/lib/consent.ts` CONSENT_ITEMS)의 label · legalBasis ·
required · sensitive 를 옮긴 것이다. 화면이 이 응답을 그리게 되면(#90) 그쪽 상수를 지운다.

🚨 순서가 화면 순서다. 계정 동의 → 아이 동의, 민감정보(child_health)는 마지막이다.
🚨 여기 없는 scope 는 약관이 등록돼 있어도 응답에 나가지 않는다. 동의를 받지 않는
   scope(`quality_improve`)가 화면에 새어 나가지 않게 하려는 것이다.
"""

from dataclasses import dataclass

from app.domains.consent.models import ConsentScope


@dataclass(frozen=True)
class ScopeInfo:
    label: str
    """법적 표기. 동의 화면의 체크박스 라벨이고 전문 시트의 제목이다."""
    legal_basis: str | None
    """화면에 그대로 보여주는 근거 조문. 없으면 보여주지 않는다."""
    required: bool
    """없으면 서비스가 성립하지 않는가. 제출을 막는 것은 이 값뿐이다."""
    sensitive: bool
    """민감정보라 다른 동의와 구분해서 받아야 하는가 (개인정보 보호법 제23조)."""


SCOPE_CATALOG: dict[ConsentScope, ScopeInfo] = {
    ConsentScope.SERVICE_TERMS: ScopeInfo(
        label="서비스 이용약관",
        legal_basis=None,
        required=True,
        sensitive=False,
    ),
    ConsentScope.PRIVACY_ACCOUNT: ScopeInfo(
        label="개인정보 수집·이용 (보호자 본인)",
        legal_basis=None,
        required=True,
        sensitive=False,
    ),
    ConsentScope.CHILD_BASIC: ScopeInfo(
        label="개인정보 수집·이용 (아이 기본정보)",
        legal_basis="개인정보보호법 제22조의2 (만 14세 미만 아동의 법정대리인 동의)",
        required=True,
        sensitive=False,
    ),
    ConsentScope.CHILD_HEALTH: ScopeInfo(
        label="민감정보 처리 (아이 건강·알레르기)",
        legal_basis="개인정보보호법 제23조 (민감정보의 처리, 별도 동의)",
        required=True,
        sensitive=True,
    ),
}
"""dict 는 넣은 순서를 지킨다 — 이 순서가 응답 순서다."""
