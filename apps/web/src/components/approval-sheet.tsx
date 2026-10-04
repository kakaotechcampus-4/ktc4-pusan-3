"use client";

import { EventDraftList } from "@/components/event-draft-list";
import { BottomSheet } from "@/components/ui/bottom-sheet";
import { Button } from "@/components/ui/button";
import type { CreateEventDraftsResponse } from "@/lib/api";

/**
 * 06 승인 시트 — **승인 게이트 ㉠ 이 여기 있다** (CLAUDE.md §2 · §3).
 *
 *   ㉠ 캘린더 쓰기   초안 제출 (`submitEventDraft`)
 *
 * 🚨 **게이트 ㉡(알레르기)는 여기 없다.** 그건 제안을 **채택할 때** 따로 묻는다
 *    (`safety-check-sheet.tsx` · #151) — 채택과 일정 만들기가 갈리면서, 초안을 만든 뒤에 물으면
 *    **일정을 안 만드는 보호자에게는 영영 안 묻게** 된다. 알레르기는 캘린더가 아니라
 *    그 음식을 먹이는 일에 걸린 위험이다.
 *    ⚠️ 그래서 이 시트에는 **막힌 초안이라는 상태가 없다** — 알레르기가 확인된 제안은 채택되지
 *    않아서 초안 자체가 안 만들어진다.
 *
 * 🚨 **초안을 만드는 호출은 게이트가 아니다** — #121 에서 **쓰기를 뗐고**, 그 응답은 저장된
 *    `event` 가 아니라 초안이다.
 *
 * 🚨 **넣는 버튼은 시트 바닥이 아니라 카드 안에 있다.** 제출은 건별이다 (9/21 회의) —
 *    바닥에 하나 더 두면 같은 일을 하는 버튼이 한 화면에 둘이 된다.
 * 🚨 **낙관적 업데이트를 쓰지 않는다.** 서버가 확정하기 전에 화면이 확정된 것처럼 보이면
 *    "되돌릴 수 없는 것은 사람이 승인한다" 가 시각적으로 깨진다.
 * 🚨 **스크림·ESC 로 닫히지 않는다** (`dismissible={false}`). 실수로 닫혀서 초안이
 *    사라지는 경로를 만들지 않는다.
 */
export function ApprovalSheet({
  open,
  onClose,
  childId,
  result,
}: {
  open: boolean;
  onClose: () => void;
  childId: string;
  /**
   * 채택한 제안들을 바꾼 결과. 🚨 초안이다 — 저장된 일정이 아니다.
   * 🚨 **초안이 여러 장일 수 있다** — `food` 는 한 끼로 묶이고 나머지는 고른 수만큼이다.
   */
  result: CreateEventDraftsResponse;
}) {
  return (
    <BottomSheet
      open={open}
      onClose={onClose}
      dismissible={false}
      title="캘린더에 넣을까요"
      description="넣기 전에는 아무것도 저장되지 않아요."
      footer={
        <Button variant="secondary" block onClick={onClose}>
          닫기
        </Button>
      }
    >
      {/* 🚨 초안이 여러 장이면 **한 장씩 넘긴다** (`event-draft-list.tsx` 머리말).
          제출 버튼이 그 안에 있고, 그게 승인 게이트 ㉠ 이다. */}
      <EventDraftList
        childId={childId}
        incoming={result.drafts}
        origin="suggestion"
        found="고른 제안으로 저장될 내용을 만들었어요."
      />
    </BottomSheet>
  );
}
