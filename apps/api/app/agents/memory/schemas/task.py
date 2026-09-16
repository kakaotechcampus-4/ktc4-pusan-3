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
