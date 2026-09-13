"""DB 세션 주입 — 라우터는 `session: SessionDep` 한 줄만 적는다.

Spring 대응: @PersistenceContext EntityManager 또는 생성자 주입.
    Spring 은 타입만 보고 컨테이너가 빈을 찾아 넣지만, FastAPI 에는 컨테이너가
    없어서 "이 값을 만드는 함수" 를 Depends 로 직접 지목한다.

Annotated[타입, Depends(함수)] = "타입 + 만드는 법" 을 한 이름으로 묶은 별칭이다.
테스트는 그 함수를 키로 바꿔 끼운다 (tests/conftest.py 의 dependency_overrides).
"""

from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.infra.db.session import get_session

SessionDep = Annotated[AsyncSession, Depends(get_session)]
"""사용 예: `async def handler(session: SessionDep) -> ...`

세션을 만들기만 하고 연결은 첫 쿼리에서 열린다(lazy). 그래서 DB 를 쓰지 않는
엔드포인트가 이 의존성을 달고 있어도 커넥션이 새지 않는다.
"""
