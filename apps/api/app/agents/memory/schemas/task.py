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

    @property
    def open_lookup_edit(self) -> bool:
        """수정/삭제 묶음을 열지 여부. 조각 중 lookup_edit이 있을 때만 연다."""
        return any(hint.work == WorkType.LOOKUP_EDIT for hint in self.hints)


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
