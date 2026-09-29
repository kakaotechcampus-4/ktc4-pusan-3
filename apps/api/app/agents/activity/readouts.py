"""Activity 의 코드 상수 문구. 모델을 거치지 않고 화면에 그대로 나간다.

테스트가 글자 단위로 비교한다. 문구를 바꾸면 설계 문서 D7 도 같이 고친다.
"""

from app.agents.common.readout import ReadoutCatalog, ReadoutText

# 알레르기 조회에 실패했다. 재료 후보만 빼서는 환경 알레르기를 못 막아서 전부 닫는다 (D7)
BLOCKED_SAFETY = "blocked.safety"

READOUTS = ReadoutCatalog(
    texts={
        BLOCKED_SAFETY: ReadoutText(
            key=BLOCKED_SAFETY,
            template="알레르기 정보를 확인할 수 없어서 놀이를 추천해 드릴 수 없어요.",
            kind="blocked",
        ),
    }
)
