"""Supervisor에서 Memory로 오는 작업.

Supervisor는 작업 종류(observe · schedule · lookup_edit)까지만 정한다.
어느 테이블에 어떤 인자로 넣을지는 Memory가 정한다.
Supervisor가 도메인까지 정해 tool을 좁히면 "책 읽었어"를 activity로 잘못 보냈을 때
Memory가 education으로 고칠 수 없다.

hints는 참고용으로, Memory는 원문 전체에서 기록할 것을 찾는다.
"""

from dataclasses import dataclass
from enum import StrEnum


class WorkType(StrEnum):
    OBSERVE = "observe"  # 이미 일어난 일
    SCHEDULE = "schedule"  # 일정, 준비물 등록
    LOOKUP_EDIT = "lookup_edit"  # 저장된 내역 조회,수정,삭제


@dataclass(frozen=True)
class MemoryHint:
    text: str  # RECORD 조각 원문
    work: WorkType


@dataclass(frozen=True)
class MemoryTask:
    raw_text: str  # 원문 전체
    hints: tuple[MemoryHint, ...] = ()  # 참고용
    # Supervisor 가 unclear 로 판정한 조각 원문. 모델에게 보여 주지 않고 코드만 쓴다.
    # 보호자 자신의 얘기·감상이라 아이 관찰로 저장하지 않는다
    unclear: tuple[str, ...] = ()
    # 기록 조각 없이 unclear 조각만 있고, 조각들이 원문을 다 덮었다(routing 이 정한다).
    # 강등(Supervisor 실패)도 힌트가 비지만 그건 못 본 것이라 unclear 가 없다. 이쪽은 보고 나서
    # 아이 기록이 아니라고 한 것이라 관찰을 저장·수정하지 않는다. 조각에 안 들어간 글자가 있으면
    # Supervisor 가 놓친 기록일 수 있어 False 로 두고 unclear 조각만 막는다
    unclear_only: bool = False

    @property
    def open_lookup_edit(self) -> bool:
        """수정/삭제 묶음을 열지 여부.

        lookup_edit 조각이 있으면 연다. Supervisor 가 기록 후보를 확실히 못 정한 경우
        (힌트가 없거나 unclear 조각이 있을 때)도 연다. "~를 ~로 바꿔줘" 가 사건 서술 + unclear 로
        쪼개지면 수정 tool 이 닫혀 새 행이 생겼다. 수정인지는 코드가 판단하지 않고 Memory 가 정한다.
        """
        return (
            not self.hints
            or bool(self.unclear)
            or any(hint.work == WorkType.LOOKUP_EDIT for hint in self.hints)
        )


@dataclass(frozen=True)
class PendingMemoryContext:
    """되묻고 멈춘 자리. 다음 입력이 이 맥락 위에 얹힌다.

    hint_text 는 아직 저장하지 못한 조각 하나다. 발화 원문 전체를 담지 않는다.
    다만 Supervisor 가 조각을 못 나눈 강등 경로에서는 원문 전체가 한 조각이고, whole 이 True 다.
    transcript는 이전 (질문, 답)을 순서대로 담는다. 코드가 채우고 모델이 쓰지 않는다.
    """

    hint_text: str
    question: str
    work: WorkType
    transcript: tuple[str, ...] = ()
    # 원문 전체가 한 조각. 그중 하나라도 저장했으면 맥락을 만들지 않으므로,
    # True 면 조각 안의 기록은 아직 하나도 저장되지 않았다
    whole: bool = False
    # unclear 조각만 있던 입력에서 아이 얘기인지 물은 것.
    # 보호자가 아니라고 하면 저장하지 않고 끝난다
    unclear: bool = False
