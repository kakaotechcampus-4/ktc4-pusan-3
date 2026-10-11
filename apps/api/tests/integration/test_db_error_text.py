"""DB 오류 글에 바인드 값이 실리지 않는다 — 엔진의 hide_parameters

SQLAlchemy 는 DB 오류 글 끝에 [parameters: (...)] 로 INSERT 의 값을 전부 싣는다 — 부르는
이름 · 원문 같은 것까지. 중복 키 DETAIL 은 키 칸만 보여 주지만 이 줄은 모든 칸을 보여 준다.
API 의 DB 오류 처리기는 글을 아예 찍지 않지만, 처리기를 거치지 않고 로그로 새는 길이 남아
있을 수 있어 엔진에서 한 번 더 막는다.

🚨 이것만으로는 부족하다 — PostgreSQL 이 보낸 DETAIL 줄의 값(회원번호 등)은 그대로 남는다.
   그래서 글을 찍지 않는 처리기가 따로 있다 (app/api/errors.py).
"""

import pytest
from sqlalchemy.exc import IntegrityError

from app.domains.identity.models import AuthIdentity, AuthProvider, Parent


async def test_db_error_text_does_not_carry_bound_values(session):
    """중복 키 오류에서 DETAIL 에 안 나오는 값(두 번째 보호자 id)이 글에 없어야 한다."""
    first, second = Parent(), Parent()
    session.add_all([first, second])
    await session.flush()
    session.add(
        AuthIdentity(parent_id=first.id, provider=AuthProvider.KAKAO, provider_user_id="dup-1")
    )
    await session.flush()

    session.add(
        AuthIdentity(parent_id=second.id, provider=AuthProvider.KAKAO, provider_user_id="dup-1")
    )
    with pytest.raises(IntegrityError) as caught:
        await session.flush()

    assert str(second.id) not in str(caught.value)
