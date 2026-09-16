"""모든 도메인 모델을 Base.metadata 에 등록한다.

🚨 이 모듈을 import 하지 않으면 "그 요청이 건드리는 테이블" 만 메타데이터에 올라온다.
   SQLAlchemy 의 ForeignKey("child.id") 는 문자열이라 flush 시점에 metadata 에서 찾는데,
   child 모델이 한 번도 import 되지 않았으면 NoReferencedTableError 로 죽는다.
   consent 를 만드는 가입 트랜잭션이 실제로 이것 때문에 깨졌다.

alembic/env.py 도 여기를 쓴다. 목록이 두 벌이면 한쪽만 늘어난 날 운영과 마이그레이션이
서로 다른 스키마를 본다. 새 도메인을 만들면 이 파일에 한 줄 추가하면 된다.
"""

from app.domains.child import models as child  # noqa: F401
from app.domains.consent import models as consent  # noqa: F401
from app.domains.correction import models as correction  # noqa: F401
from app.domains.identity import models as identity  # noqa: F401
from app.domains.memory.observation import models as observation  # noqa: F401
from app.domains.memory.profile import models as profile  # noqa: F401
from app.domains.safety import models as safety  # noqa: F401
from app.domains.schedule import models as schedule  # noqa: F401
from app.domains.suggestion import models as suggestion  # noqa: F401
