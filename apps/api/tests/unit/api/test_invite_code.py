"""초대 코드 정규화 — 프론트 apps/web/src/lib/invite-code.ts 와 같은 규칙 (invite-v1.md §4)."""

import pytest

from app.api.v1.routers.invites import generate_invite_code, normalize_invite_code


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("ABCD-1234", "ABCD1234"),  # 화면 표시 형식
        ("abcd 1234", "ABCD1234"),  # 소문자 · 공백
        ("O0IL1", "00111"),  # 혼동 문자는 버리지 않고 옮긴다
        ("ABCD-1234-EXTRA", "ABCD1234EXTRA"),  # 길이는 자르지 않는다 — 없는 코드가 된다
    ],
)
def test_정규화(raw, expected):
    assert normalize_invite_code(raw) == expected


def test_발행한_코드는_정규화해도_그대로다():
    """저장하는 해시와 받은 코드의 해시가 같으려면 발행 값이 이미 정규화된 모양이어야 한다."""
    for _ in range(100):
        code = generate_invite_code()
        assert normalize_invite_code(code) == code
