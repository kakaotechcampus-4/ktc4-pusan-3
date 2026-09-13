"""엔드포인트 여러 곳이 함께 쓰는 요청·응답 조각 — 계약서 §01 · §02

Spring 대응: 공통 DTO 패키지. Pydantic BaseModel 하나가 DTO + Bean Validation +
    Jackson 직렬화를 겸한다.

에러 봉투 모델은 app/api/errors.py 에 두고 여기서 다시 내보낸다.
응답 생성과 OpenAPI 문서가 같은 모델을 써서 정의가 두 벌이 되는 것을 막는다.
"""

from pydantic import BaseModel

from app.api.errors import ErrorBody, ErrorEnvelope

__all__ = ["ErrorBody", "ErrorEnvelope", "Ref"]


class Ref(BaseModel):
    """도메인 간 참조 — 계약서 §01 "Ref".

    observation 이 4개 테이블로 나뉘어 있어 id 만으로는 어느 테이블인지 알 수 없다.
    그래서 모든 참조는 {kind, id} 두 칸을 함께 싣는다.

    kind 허용값을 enum 으로 좁히는 것은 계약서 §02 공통 타입 5개를 옮길 때 함께
    한다 — 지금 좁히면 그때 두 번 고친다.
    """

    kind: str
    id: str
