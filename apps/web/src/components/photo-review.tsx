"use client";

import { useQueryClient } from "@tanstack/react-query";
import { ChevronDown, ChevronUp } from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";

import { PhotoEntrySheet } from "@/components/photo-entry-sheet";
import { PhotoPreview } from "@/components/photo-preview";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Chip, ChipRow } from "@/components/ui/chip";
import { ICON_SIZE, ICON_STROKE } from "@/components/ui/icon";
import { PhotoCard } from "@/components/ui/photo-card";
import { Spinner } from "@/components/ui/spinner";
import {
  createIdempotencyKeyHolder,
  isApiError,
  qk,
  submitEventDraft,
  type IdempotencyKeyHolder,
  type ParsedEvent,
  type PhotoCommitRequest,
  type PhotoEntry,
  type PhotoLane,
} from "@/lib/api";
import { cn } from "@/lib/cn";
import { seoulDate } from "@/lib/event-draft";
import {
  formatDateTime,
  formatDay,
  formatDayRange,
  formatEventTime,
  formatTimeOfDay,
} from "@/lib/format";
import { playEnter, useFlip } from "@/lib/motion";
import {
  canBecomeEvent,
  entryDateLabel,
  entryDateRole,
  eventBody,
  eventWhen,
  initialPlan,
  offBecausePast,
  planEndsAt,
  planLocks,
  planStartsAt,
  readMoment,
  readSpan,
  startsChecked,
  type AddedEvent,
  type EventPlan,
} from "@/lib/photo-entry";

/**
 * 08 사진으로 적기 — 읽어낸 것을 보호자가 확인하는 칸.
 *
 * ## 🚨 맨 아래 버튼이 승인 게이트 ㉠ 이 될 때가 있다 (#824)
 *
 * 목록 줄마다 "캘린더에 넣기" 체크가 있고, 맨 아래 버튼 하나가 **체크한 일정을 넣고 → 읽은 내용을
 * 저장한다.** 체크한 것이 있으면 그 버튼이 캘린더에 쓰는 되돌릴 수 없는 지점이라 `btn-approve` +
 * `caution` 이고, 없으면 그냥 "저장" 이다 (읽은 내용 저장은 게이트가 아니다).
 *
 * 🚨 **게이트를 늘린 것이 아니다.** 한동안 이 게이트는 항목마다 고치기 시트 안에 있었다 — 일정이
 *    셋이면 시트를 세 번 열었고, 저장이 두 군데라 헷갈렸다. 같은 게이트를 목록 아래로 옮겼다
 *    (최상위 CLAUDE.md §2 — 늘리지도 줄이지도 않는다).
 * 🚨 **자동으로 넣지 않는다.** 날짜를 읽은 일정은 체크가 켜진 채로 시작하지만(`startsChecked`) 넣는 것은
 *    보호자가 버튼을 누를 때다. 그래서 버튼 **바로 위에** 무엇이 언제로 들어가는지 목록이 선다 —
 *    켜진 줄이 접힌 "잘 읽었어요" 안에 있어도 버튼 앞에서 보인다.
 * 🚨 **캘린더에 쓰는 문은 `POST /children/{cid}/events` 하나다.** 체크한 개수만큼 부르고, `commit` 은
 *    캘린더에 쓰지 않는다 (`docs/notice/notice-ocr-v1.md` §2).
 * 🚨 **순서는 일정 먼저, 그다음 commit 이다.** 받은 `event.id` 를 그 항목의 `event_id` 로 실어야 서버가
 *    일정에 공지 항목을 출처로 단다 (#792).
 * 🚨 **하나라도 못 넣으면 commit 하지 않는다.** 화면에 남아서 넣은 것은 "넣었어요" 로 잠그고 못 넣은
 *    것만 다시 넣는다. 항목마다 Idempotency-Key 가 따로라 다시 눌러도 넣은 것이 두 번 들어가지 않는다.
 *
 * 🚨 **한 장에서 항목이 여러 개 나온다.** 알림장 한 장에 일정이 여러 개 적혀 있고 식단표는
 *    거의 한 달치다. 그래서 화면이 두 무리로 가른다 —
 *    ㉠ **확인이 필요한 것**(값을 못 읽음)을 위에 펼쳐 두고,
 *    ㉡ **잘 읽은 것**은 아래에 **접어** 둔다. 스무 개가 한 줄씩 늘어서면 정작 봐야 할
 *    두세 개가 묻힌다 — 부모가 볼 것은 "기계가 못 읽은 곳" 이다.
 *
 * 🚨 **확인하지 않은 항목은 저장되지 않는다.** 고치기 시트에서 확인을 눌러야 저장 대상이 된다 —
 *    못 읽은 값을 그대로 넣으면 "승인 전에는 저장되지 않아요" 가 문구만 남는다.
 *    화면이 몇 건이 빠지는지 숫자로 말한다. 같은 이유로 확인 전에는 캘린더에 넣기도 잠긴다.
 *
 * 🚨 **사진과 읽어낸 글자는 한 카드다.** 부모가 확인해야 하는 것이 "사진에 있는 것" 과
 *    "읽어낸 것" 의 **대조**라서, 둘을 따로 세우면 그 관계가 배치가 아니라 문구로만 남는다.
 *
 * 🚨 **도메인 색을 쓰지 않는다.** lane 은 도메인(`food`·`activity`·`growth`·`health`)이
 *    아니다 — 이름이 겹치는 `activity` 가 있지만 여기서는 "아이 활동 사진" 이라는 읽기 방식이다.
 *
 * 🚨 **`raw_text` 는 OCR 로 들어온 외부 텍스트다.** 그대로 텍스트로만 그린다 —
 *    `dangerouslySetInnerHTML` 를 쓰지 않는다 (apps/web/CLAUDE.md §4).
 */

/**
 * 🚨 **여기서 lane 을 바꾸지 않는다.** 부모가 시트에서 **고르고 시작했기 때문**이다
 *    (`PhotoSourceSheet` 1단계) — 무엇을 찍었는지는 찍은 사람이 알고, 그 선언이 업로드에
 *    실려 무엇을 읽을지까지 정한다. 결과가 "다른 종류로 읽혔을" 자리가 없다.
 *    한동안 서버 추측(`lane` 이벤트)과 어긋나면 되묻는 줄을 세워 뒀는데, 고를 때 이미 물은
 *    것을 결과 화면에서 또 묻는 것이라 뺐다. 🚨 **서버 추측은 선언을 덮지 않는다** 는 규칙은
 *    그대로고, 그 사실은 화면이 아니라 계약 테스트가 지킨다 (`mocks/contract.test.ts`).
 */
interface LaneCopy {
  read: string;
  note: string;
  footnote: string;
}

const LANE: Record<PhotoLane, LaneCopy> = {
  document: {
    read: "알림장이나 식단표로 읽었어요.",
    note: "문서에서는 글자만 읽어요. 준비물과 일시로 정리하고, 아이의 기록으로는 쌓지 않아요.",
    footnote:
      "문서에서 읽은 것은 기관이 알려준 사실로만 저장해요. 아이의 성향이나 선호로 해석하지 않아요.",
  },
  activity: {
    read: "아이 활동 사진으로 읽었어요.",
    note: "사진에서 활동 태그만 뽑아요. 얼굴이나 사람은 분석하지 않아요.",
    footnote: "태그로 아이의 성향이나 발달을 정하지 않아요. 알레르기나 건강 정보로도 쓰지 않아요.",
  },
};

const ENTRY_KIND_LABEL: Record<PhotoEntry["kind"], string> = {
  event: "일정",
  supply: "준비물",
  meal: "급식",
  todo: "할 일",
  info: "안내",
};

/**
 * 저장이 왜 안 됐는가. 🚨 **화면이 문구를 만든다** — 서버 문구를 그대로 싣지 않는다 (#815).
 *
 * - `failed`: 다시 누르면 될 수 있다
 * - `expired`: 읽은 결과가 서버에서 사라졌다(30분 · `404 not_found`). 🚨 다시 눌러도 같아서 버튼을 잠근다
 */
export type PhotoCommitFailure = "failed" | "expired";

export interface PhotoReviewProps {
  /**
   * 🚨 **부모가 시트에서 고른 값.** 이 화면의 정본이고 **여기서 바뀌지 않는다** (위 주석).
   *    서버가 보내는 `lane` 추측은 이 화면이 쓰지 않는다 — 계약서에 남아 있을 뿐이다.
   */
  lane: PhotoLane;
  childId: string;
  parsed: ParsedEvent;
  /** 이 사진을 어느 날에 남기는지. 캘린더에서 들어왔으면 그날, 아니면 오늘이다. */
  date: string;
  onCommit: (body: PhotoCommitRequest) => void;
  committing: boolean;
  commitError: PhotoCommitFailure | null;
  /** 저장하지 않고 03 홈으로. */
  onGoHome: () => void;
  /** 4:3 칸에 그릴 사진. 업로드한 파일의 objectURL 이다. */
  previewUrl: string;
}

/** 목록 줄의 "캘린더에 넣기" 한 칸. 급식 · 안내 · 이미 넣은 줄에는 없다. */
interface CalendarPick {
  checked: boolean;
  /** 지금 넣을 수 없는 까닭 (`planLocks`). 있으면 체크가 잠긴다. */
  locks: string[];
  /** 지난번에 넣지 못한 까닭. */
  failure: string | undefined;
  /** 지난 일정이라 처음에 꺼 둔 줄이고, 보호자가 아직 건드리지 않았다 (`pastOff`). */
  pastOff: boolean;
}

export function PhotoReview({
  lane,
  childId,
  parsed,
  date,
  onCommit,
  committing,
  commitError,
  onGoHome,
  previewUrl,
}: PhotoReviewProps) {
  const queryClient = useQueryClient();
  /**
   * 🚨 **읽어낸 항목의 정본은 여기다.** 부모가 고치면 이 배열이 바뀌고, 저장은 이 값을 보낸다 —
   *    서버가 보낸 `parsed` 를 다시 읽지 않는다.
   */
  const [entries, setEntries] = useState<PhotoEntry[]>(() => parsed.entries ?? []);
  const [tags, setTags] = useState<string[]>([]);
  const [editing, setEditing] = useState<PhotoEntry | null>(null);
  /**
   * 항목마다 캘린더에 넣을 때의 값 (`EventPlan`). 고치기 시트의 "캘린더에 넣을 때" 칸이 고친다.
   * 🚨 **없는 항목은 처음 값이다** (`initialPlan`) — 열어 보지 않은 항목도 읽은 값 그대로 넣을 수 있다.
   */
  const [plans, setPlans] = useState<Record<string, EventPlan>>({});
  /**
   * "캘린더에 넣기" 를 켠 항목. 🚨 **처음에는 시작 날짜를 읽은 일정만이다** (`startsChecked`) — 할 일은
   *    보호자가 고른다. 켠 것은 넣은 것이 아니다. 넣는 것은 맨 아래 버튼이다.
   */
  const [picked, setPicked] = useState<Set<string>>(
    () => new Set((parsed.entries ?? []).filter(startsChecked).map((entry) => entry.id)),
  );
  /**
   * 지난 일정이라 처음에 꺼 둔 줄 (`offBecausePast` · #828 리뷰). 그 줄이 "지난 일정이라 꺼 두었어요." 를
   * 말한다. 🚨 **보호자가 그 줄을 켜고 끄거나 고치면 빠진다** — 그 뒤로는 앱이 끈 것이 아니고, 날짜를 고쳤으면
   *    `past` 는 처음 읽은 날짜의 판정이라 더는 맞지 않는다. 화면이 고친 날짜로 다시 견주지 않는다 (§4).
   */
  const [pastOff, setPastOff] = useState<Set<string>>(
    () => new Set((parsed.entries ?? []).filter(offBecausePast).map((entry) => entry.id)),
  );
  /**
   * 이미 캘린더에 넣은 항목 → **넣은 일정.** 🚨 **저장(commit)과 다른 축이다** — 일정을 다 넣고 commit 이
   *    실패하면 "일정은 들어갔는데 공지는 저장 안 된" 상태가 된다. 한 상태로 묶으면 그것을 표현할 수 없다.
   * 🚨 **건수만 들지 않는다.** 목록의 그 줄이 "언제로 넣었는지" 까지 말해야 부모가 확인하러
   *    캘린더에 가지 않는다.
   */
  const [eventAdded, setEventAdded] = useState<Record<string, AddedEvent>>({});
  /** 지난번에 넣지 못한 항목 → 까닭. 🚨 이것이 하나라도 있으면 commit 하지 않았다. */
  const [eventFailed, setEventFailed] = useState<Record<string, string>>({});
  /** 일정을 넣는 중인가. commit 중(`committing`)과 갈라 둔다 — 버튼이 지금 무엇을 하는지 말한다. */
  const [adding, setAdding] = useState(false);
  /**
   * 🚨 **항목마다 Idempotency-Key 가 따로다.** 하나의 키로 여러 일정을 보내면 둘째부터 "같은 키 · 다른 본문"
   *    422 다. 실패한 뒤 다시 누르면 같은 항목은 같은 키로 간다 — 응답만 못 받고 들어간 일정이 두 번 들어가지
   *    않는다. 본문을 고치면 키가 바뀐다 (`current(payload)` · apps/web/CLAUDE.md §3).
   * 🚨 `useState` 의 게으른 초기화로 화면 수명 동안 한 벌이다 (`useIdempotencyKey` 와 같은 이유).
   */
  const [eventKeys] = useState(() => new Map<string, IdempotencyKeyHolder>());
  /**
   * 🚨 **두 번 빨리 누르면 넣기가 두 벌 돈다.** 버튼은 `adding` 이 칠해진 뒤에야 잠기는데, 그 사이의 두 번째
   *    누름이 같은 키로 같은 일정을 동시에 보낸다(서버 409 in_progress). 상태가 아니라 ref 로 바로 막는다.
   */
  const running = useRef(false);
  /**
   * 🚨 **넣는 도중에 화면을 떠나면 남은 일정은 보내지 않는다** (#828 리뷰). 머리의 "뒤로 가기" 버튼이나
   *    기기 뒤로가기로 화면이 사라져도 `addEvents` 의 반복은 그대로 돌아, 누르고 바로 마음을 바꿔
   *    떠났는데 켠 일정이 전부 들어갔다. 이미 보내고 있던 한 건은 되돌릴 수 없어 그대로 두고, 다음
   *    건부터 멈춘다. 그 두 뒤로 가기는 넣는 동안에도 잠그지 않는다.
   * 🚨 마운트 때 `true` 로 다시 칠한다 — Strict Mode 는 마운트 → 정리 → 마운트를 한 번 더 돈다.
   */
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  const [showChecked, setShowChecked] = useState(false);
  /**
   * 자리 옮김 (디자인 시스템 §8). 확인한 줄이 "확인이 필요해요" 에서 "잘 읽었어요" 로 옮겨 가고, 빼거나
   * 펼친 줄의 자리를 나머지가 메운다. 두 묶음을 함께 감싸야 묶음을 건너가는 줄이 같은 id 로 이어진다.
   */
  const flipRef = useFlip<HTMLDivElement>();

  const copy = LANE[lane];
  const busy = adding || committing;

  function planOf(entry: PhotoEntry): EventPlan {
    return plans[entry.id] ?? initialPlan(entry);
  }

  /** 줄의 "캘린더에 넣기" 칸. 급식 · 안내 · 이미 넣은 줄은 `null` 이다. */
  function pickOf(entry: PhotoEntry): CalendarPick | null {
    if (!canBecomeEvent(entry) || eventAdded[entry.id]) return null;
    const locks = planLocks(entry, planOf(entry));
    return {
      // 🚨 잠긴 줄은 켜져 있어도 넣지 않는다 — 켜 둔 뒤 날짜를 지우면 그 줄이 조용히 빠진다고 말하는 대신
      //    꺼진 채로 잠기고, 왜 잠겼는지를 그 줄이 말한다.
      checked: picked.has(entry.id) && locks.length === 0,
      locks,
      failure: eventFailed[entry.id],
      pastOff: pastOff.has(entry.id),
    };
  }

  function forgetFailure(id: string) {
    setEventFailed((prev) => {
      if (!(id in prev)) return prev;
      const next = { ...prev };
      delete next[id];
      return next;
    });
  }

  function forgetPastOff(id: string) {
    setPastOff((prev) => {
      if (!prev.has(id)) return prev;
      const out = new Set(prev);
      out.delete(id);
      return out;
    });
  }

  function togglePick(id: string, next: boolean) {
    setPicked((prev) => {
      const out = new Set(prev);
      if (next) out.add(id);
      else out.delete(id);
      return out;
    });
    forgetFailure(id);
    forgetPastOff(id);
  }

  function saveEntry(next: PhotoEntry, plan: EventPlan | null) {
    setEntries((prev) => prev.map((e) => (e.id === next.id ? next : e)));
    if (plan) setPlans((prev) => ({ ...prev, [next.id]: plan }));
    /**
     * 🚨 **고친 값으로 잠기면 켜 둔 것을 끈다.** 켠 채로 두면 나중에 값을 채워 풀리는 순간 보호자가 다시 켜지
     *    않았는데 저절로 켜진다. 한 번 잠긴 줄은 보호자가 다시 켠다.
     */
    if (canBecomeEvent(next) && planLocks(next, plan ?? planOf(next)).length > 0) {
      togglePick(next.id, false);
    }
    // 고친 줄의 지난 실패는 지운다 — 고친 값으로는 될 수 있고, 남겨 두면 고친 뒤에도 못 넣었다고 말한다.
    forgetFailure(next.id);
    forgetPastOff(next.id);
    setEditing(null);
  }

  function removeEntry(id: string) {
    setEntries((prev) => prev.filter((e) => e.id !== id));
    togglePick(id, false);
    setEditing(null);
  }

  const needsReview = entries.filter((e) => e.needs_review);
  const checked = entries.filter((e) => !e.needs_review);

  /**
   * 이번에 넣을 일정. 🚨 **목록 순서 그대로다** — 버튼 위 목록과 넣는 순서가 같아야 실패한 줄을 찾기 쉽다.
   * 🚨 저장 대상(확인한 항목)만이다. 확인이 필요한 항목은 `planLocks` 가 먼저 막지만 한 번 더 거른다.
   */
  const toAdd = lane === "document" ? checked.filter((entry) => pickOf(entry)?.checked) : [];
  /** 버튼 위 "캘린더" 줄에 서는 것 — 이미 넣은 것과 이번에 넣을 것, 목록 순서대로. */
  const calendarEntries = checked.filter((entry) => eventAdded[entry.id] || toAdd.includes(entry));
  const addedCount = Object.keys(eventAdded).length;
  const failedCount = Object.keys(eventFailed).length;

  /**
   * 🚨 **`attach_to_calendar` 는 활동 lane 만의 것이다** — 그 lane 에서는 "사진을 그날
   *    캘린더에 함께 남길까" 라는 뜻이다 (계약서 §09 의 부수 효과 표).
   *
   * 🚨 **문서 lane 은 언제나 `false` 다.** 일정은 이 화면이 `POST /events` 로 따로 넣는다 —
   *    commit 이 일정까지 만들면 같은 일을 두 곳이 하게 된다 (`docs/notice/notice-ocr-v1.md` §2).
   *
   * 🚨 **날짜를 만들지 않는다.** 활동은 화면이 들고 온 날짜를 그대로 쓴다 (apps/web/CLAUDE.md §4).
   */
  const attachDate = lane === "activity" ? date : null;
  const attachToCalendar = lane === "activity" && attachDate !== null;

  /**
   * 🚨 **활동 lane 은 태그를 하나도 안 고르면 저장할 수 없다.** 그 상태로 눌리면 화면이 바로
   *    위에서 한 "고르지 않은 태그는 저장하지 않아요" 를 스스로 뒤집는다.
   *    문서 lane 은 확인된 항목이 하나라도 있어야 한다 — 빈 저장을 만들지 않는다.
   * 🚨 **읽은 결과가 사라졌으면(`expired`) 잠근다.** 다시 눌러도 같은 404 다.
   */
  const hasSomethingToSave = lane === "activity" ? tags.length > 0 : checked.length > 0;
  const canSave = !busy && hasSomethingToSave && commitError !== "expired";

  /** 넣을 일정을 차례로 넣는다. 넣은 것을 모두 돌려주고, 하나라도 못 넣었으면 `null` 이다. */
  async function addEvents(): Promise<Record<string, AddedEvent> | null> {
    let added = eventAdded;
    const failures: Record<string, string> = {};
    /**
     * 🚨 **하나씩, 끝까지 간다.** 하나가 실패해도 나머지는 넣는다 — 거기서 멈추면 뒤의 줄은 시도도 안 한
     *    채로 "못 넣었어요" 가 된다. 멈추는 것은 화면을 떠났을 때뿐이다(`mounted`). 동시에 보내지 않는 것은 실패한 줄과 넣은 줄이 목록 순서대로
     *    갈려야 보호자가 따라 읽을 수 있어서다. 많아야 몇 건이다.
     */
    for (const entry of toAdd) {
      if (!mounted.current) break;
      const body = eventBody(entry, planOf(entry));
      if (body === null) continue; // 잠긴 줄은 `toAdd` 에 없다 — 타입을 좁히는 자리다.
      let holder = eventKeys.get(entry.id);
      if (!holder) {
        holder = createIdempotencyKeyHolder();
        eventKeys.set(entry.id, holder);
      }
      try {
        const res = await submitEventDraft(childId, body, holder.current(JSON.stringify(body)));
        // 🚨 성공한 뒤에만 다음 키로 넘어간다. 실패 뒤 다시 누르는 것은 재시도라 같은 키여야 한다.
        holder.rotate();
        added = {
          ...added,
          [entry.id]: {
            event_id: res.event.id,
            // 🚨 일시는 응답에서 받는다 — 같은 키 재시도면 서버는 처음 넣은 일정을 돌려준다 (#792 리뷰).
            starts_at: res.event.starts_at,
            ends_at: res.event.ends_at,
            all_day: res.event.all_day,
          },
        };
        // 🚨 넣을 때마다 바로 표시한다 — 다 끝나고 한 번에 바꾸면 넣는 동안 어디까지 됐는지 안 보인다.
        setEventAdded(added);
      } catch (error) {
        failures[entry.id] = eventFailureText(error);
      }
    }

    if (added !== eventAdded) {
      // 🚨 캘린더가 바뀌었다. commit 이 실패해 이 화면에 남아도 09 · 03 이 방금 넣은 일정을 보여야 한다.
      void queryClient.invalidateQueries({ queryKey: qk.child(childId) });
    }
    // 화면을 떠났으면 commit 하지 않는다 — 넣은 일정만 남고 공지는 저장되지 않는다.
    if (!mounted.current) return null;
    setEventFailed(failures);
    if (Object.keys(failures).length > 0) {
      // 🚨 못 넣은 줄이 접힌 묶음 안에 있으면 까닭이 안 보인다.
      setShowChecked(true);
      return null;
    }
    return added;
  }

  async function save() {
    if (running.current) return;
    if (lane === "activity") {
      onCommit({ lane, selected_tags: tags, attach_to_calendar: attachToCalendar });
      return;
    }

    let added = eventAdded;
    if (toAdd.length > 0) {
      running.current = true;
      setAdding(true);
      const result = await addEvents().finally(() => {
        running.current = false;
        setAdding(false);
      });
      // 🚨 **하나라도 못 넣었으면 저장하지 않는다.** 화면에 남아 못 넣은 것만 다시 넣는다.
      if (result === null) return;
      added = result;
    }

    onCommit({
      lane,
      /**
       * 🚨 **넣은 일정의 id 를 그 항목에 실어 보낸다** (#723). 서버가 그 일정에 이 공지 항목을
       *    출처로 단다 — 안 실으면 캘린더의 일정과 저장한 공지가 서로를 모른다.
       *    넣은 뒤 "이 항목은 저장하지 않기" 를 고른 항목은 `checked` 에 없어서 실리지 않는다.
       *    일정은 남고 출처만 비는 것이라 그대로 둔다.
       */
      entries: checked.map((entry) => ({
        ...entry,
        event_id: added[entry.id]?.event_id ?? null,
      })),
      attach_to_calendar: attachToCalendar,
    });
  }

  return (
    <div ref={flipRef} className="flex flex-col gap-6">
      {/* 사진과 거기서 읽어낸 글자가 한 덩어리다 — 이 화면에서 `accent` 는 여기 한 장뿐이다. */}
      <PhotoCard
        tone="accent"
        footer={
          <div className="flex flex-col gap-3">
            {/* 🚨 바꾸는 버튼을 두지 않는다 — 시트에서 고르고 시작했다 (위 주석). */}
            <p className="text-body text-ink">{copy.read}</p>
            <p className="text-body-sm text-ink-muted">{copy.note}</p>

            {lane === "document" && parsed.raw_text ? (
              <details
                className="group border-line border-t pt-3"
                // 펼친 원문은 투명도로 선다 (디자인 시스템 §8 "펼침"). 접을 때는 즉시다.
                // 🚨 `toggle` 이벤트는 열린 뒤 따로 늦게 와서, 그 사이 한 번 칠해지면 다 보였다가 투명으로
                //    돌아가 깜빡인다. 누른 순간 다음 프레임(칠하기 전)에 건다 — 그때는 이미 열려 있다.
                onClick={(event) => {
                  const details = event.currentTarget;
                  if (details.open) return;
                  requestAnimationFrame(() => {
                    if (details.open) playEnter(details.querySelector(":scope > p"));
                  });
                }}
              >
                {/* 🚨 원문은 **접어 둔다.** 한 달치 식단표의 원문이 펼쳐져 있으면 정작 확인할
                    항목이 화면 아래로 밀린다 — 대조가 필요할 때 여는 자리다. */}
                {/* 🚨 기본 마커를 지우고 쉐브론을 단다 — 브라우저마다 삼각형 모양이 다르고,
                    Tailwind preflight 아래에서는 아예 안 보여서 **누를 수 있다는 것이 안 보였다.**
                    `[&::-webkit-details-marker]` 는 사파리 전용 마커까지 같이 끈다. */}
                <summary className="text-caption text-ink-subtle min-h-touch flex cursor-pointer list-none items-center gap-1 [&::-webkit-details-marker]:hidden">
                  {/* 🚨 방향은 아이콘을 갈아 끼워 말한다 — 회전 애니메이션을 만들지 않는다
                      (`Select` · 위의 "잘 읽었어요" 와 같은 언어다). */}
                  <ChevronDown
                    aria-hidden
                    size={ICON_SIZE.sm}
                    strokeWidth={ICON_STROKE}
                    className="shrink-0 group-open:hidden"
                  />
                  <ChevronUp
                    aria-hidden
                    size={ICON_SIZE.sm}
                    strokeWidth={ICON_STROKE}
                    className="hidden shrink-0 group-open:block"
                  />
                  인식한 원문 보기
                </summary>
                {/* 🚨 외부 텍스트다. 텍스트로만 그린다. */}
                <p className="text-body-sm text-ink whitespace-pre-line">{parsed.raw_text}</p>
              </details>
            ) : null}
          </div>
        }
      >
        <PhotoPreview src={previewUrl} alt="방금 넣은 사진" />
      </PhotoCard>

      {lane === "activity" ? (
        <TagPicker tags={parsed.tags ?? []} selected={tags} onChange={setTags} busy={committing} />
      ) : (
        <>
          <NeedsReviewGroup
            entries={needsReview}
            added={eventAdded}
            pickOf={pickOf}
            busy={busy}
            onPick={togglePick}
            onEdit={setEditing}
          />
          <CheckedGroup
            entries={checked}
            added={eventAdded}
            pickOf={pickOf}
            busy={busy}
            onPick={togglePick}
            open={showChecked}
            onToggle={() => setShowChecked((v) => !v)}
            onEdit={setEditing}
          />
        </>
      )}

      <Card>
        <p className="text-section text-ink">이렇게 저장할게요</p>
        {/* 🚨 중립 면이다. 사실을 말하는 자리라 경고색을 쓰지 않는다.
            🚨 **넣은 일정이 있으면 "아무것도 저장되지 않았어요" 가 거짓이다** — 일부만 넣고 멈췄거나
               commit 만 실패한 뒤다. 들어간 것을 먼저 말한다. */}
        <p className="text-body-sm text-ink-muted bg-surface-muted rounded-field mt-2 px-3 py-2">
          {addedCount > 0
            ? `캘린더에 넣은 일정 ${addedCount}개 말고는 아직 저장되지 않았어요. 아래를 눌러야 저장돼요.`
            : "지금까지는 아무것도 저장되지 않았어요. 아래를 눌러야 저장돼요."}
        </p>

        <dl className="mt-4 flex flex-col gap-2">
          {lane === "activity" ? (
            <PreviewRow term="활동" value={tags.length > 0 ? tags.join(", ") : "고른 것 없음"} />
          ) : (
            <>
              <PreviewRow
                term="확인한 것"
                value={checked.length > 0 ? `${checked.length}건` : "아직 없음"}
              />
              {/* 🚨 **몇 건이 빠지는지 숫자로 말한다.** 확인 안 한 것이 조용히 사라지면
                  "승인 전에는 저장되지 않아요" 의 반대말이 된다.
                  🚨 **회색 면을 따로 두지 않고 이 목록의 한 줄로 둔다** — "확인한 것" 과 같은
                  질문("무엇이 저장되나")의 나머지 절반이라 나란히 서야 읽힌다. */}
              {needsReview.length > 0 ? (
                <PreviewRow
                  term="확인 안 한 것"
                  value={`${needsReview.length}건은 저장하지 않아요`}
                />
              ) : null}
              {/* 🚨 **게이트 앞에서 무엇이 언제로 들어가는지 하나씩 말한다** (#824). 켜진 줄이 접힌
                  "잘 읽었어요" 안에 있어도 여기서 보인다 — 개수만 보고 누르는 버튼이 되면 안 된다. */}
              <PreviewRow
                term="캘린더"
                value={
                  calendarEntries.length === 0 ? (
                    "넣지 않아요"
                  ) : (
                    // 🚨 **목록 순서 그대로다.** 넣은 것과 넣을 것을 따로 모으면 실패한 줄이 맨 아래로 밀려
                    //    위 목록과 순서가 달라진다.
                    <ul className="flex flex-col gap-1">
                      {calendarEntries.map((entry) => {
                        const got = eventAdded[entry.id];
                        return (
                          <li key={entry.id}>
                            {entry.title},{" "}
                            {got
                              ? eventWhen(got.starts_at, got.ends_at, got.all_day)
                              : planWhen(entry, planOf(entry))}
                            {got ? (
                              <span className="text-ink-muted"> 넣었어요</span>
                            ) : eventFailed[entry.id] ? (
                              <span className="text-ink-muted"> 넣지 못했어요</span>
                            ) : null}
                          </li>
                        );
                      })}
                    </ul>
                  )
                }
              />
            </>
          )}
          {attachDate ? <PreviewRow term="남기는 날" value={formatDay(attachDate)} /> : null}
          <PreviewRow
            term="출처"
            value={lane === "document" ? "기관이 보낸 문서" : "보호자가 확인한 사진 태그"}
          />
        </dl>

        {/* 🚨 실패를 빨강으로 칠하지 않는다 (디자인 시스템 §3). */}
        {failedCount > 0 ? (
          <p
            role="status"
            className="bg-surface-muted rounded-field text-body-sm text-ink-muted mt-4 p-3"
          >
            {`일정 ${failedCount}개를 넣지 못해서 아직 저장하지 않았어요. `}
            {addedCount > 0 ? `넣은 ${addedCount}개는 캘린더에 들어갔어요. ` : null}
            다시 누르면 못 넣은 것만 다시 넣어요. 빼고 저장하려면 그 줄의 체크를 꺼 주세요.
          </p>
        ) : commitError ? (
          <p
            role="status"
            className="bg-surface-muted rounded-field text-body-sm text-ink-muted mt-4 p-3"
          >
            {commitText(commitError, addedCount)}
          </p>
        ) : null}

        {/* 🚨 **게이트의 마지막 확인은 버튼 옆에 선다** (`safety-scan-review.tsx` 선례). 배너를 하나 더
            세우지 않는다 — 색은 `caution` 을 쓰되 글자 한 덩이다. 넣을 것이 없으면 게이트가 아니라서 없다. */}
        {toAdd.length > 0 ? (
          <p className="text-body-sm text-caution mt-4">
            누르면 일정 {toAdd.length}개가 캘린더에 바로 들어가요. 함께 보는 보호자에게도 보여요.
          </p>
        ) : null}

        <Button
          block
          className="mt-4"
          // 🚨 넣을 일정이 있을 때만 게이트 표식이다. 읽은 내용 저장만 하는 버튼은 게이트가 아니다.
          variant={toAdd.length > 0 ? "approve" : "primary"}
          disabled={!canSave}
          aria-busy={busy}
          onClick={() => void save()}
        >
          {busy ? <Spinner /> : null}
          {saveLabel({ lane, adding, committing, count: toAdd.length })}
        </Button>

        {lane === "activity" ? (
          <p className="text-caption text-ink-subtle mt-2">
            {attachDate
              ? `저장하면 이 사진도 ${formatDay(attachDate)} 캘린더에 함께 남아요.`
              : "남길 날짜가 없어서 캘린더에는 붙이지 않아요."}
          </p>
        ) : null}
      </Card>

      {/* 🚨 저장하지 않고 나가는 길. 확인을 묻지 않는다 — 승인 게이트는 3곳뿐이고 여기는 그곳이 아니다
          (CLAUDE.md §2). 일부만 넣고 나가도 넣은 일정은 이미 캘린더에 있어서 잃을 것이 없다.
          🚨 넣는 중에는 잠근다 — 나가면 어디까지 들어갔는지 볼 곳이 없다. */}
      <div>
        <Button variant="secondary" disabled={busy} onClick={onGoHome}>
          홈으로 돌아가기
        </Button>
      </div>

      <p className="text-caption text-ink-subtle">{copy.footnote}</p>

      <PhotoEntrySheet
        entry={editing}
        plan={editing !== null && canBecomeEvent(editing) ? planOf(editing) : null}
        added={editing !== null ? eventAdded[editing.id] : undefined}
        onClose={() => setEditing(null)}
        onSave={saveEntry}
        onRemove={removeEntry}
      />
    </div>
  );
}

/** 맨 아래 버튼의 문구. 🚨 **넣을 개수를 버튼이 말한다** — 무엇이 되는지 누르기 전에 보여야 한다. */
function saveLabel({
  lane,
  adding,
  committing,
  count,
}: {
  lane: PhotoLane;
  adding: boolean;
  committing: boolean;
  count: number;
}): string {
  if (adding) return "일정을 넣는 중이에요";
  if (committing) return "저장하는 중이에요";
  if (lane === "activity") return "이 내용으로 저장";
  return count > 0 ? `저장하고 캘린더에 ${count}개 넣기` : "저장";
}

/**
 * 일정 하나를 못 넣은 까닭.
 * 🚨 **다시 눌러도 같은 실패는 고칠 곳을 말한다** — 값이 틀린 400 · 지난 알림 422 는 같은 값으로 다시 보내면
 *    같은 응답이다 (apps/web/CLAUDE.md §3 에러). 나머지(연결 · 서버)는 다시 누르면 될 수 있다.
 */
function eventFailureText(error: unknown): string {
  if (isApiError(error, "invalid_reminder")) {
    return "고른 알림으로는 넣을 수 없어요. 고치기에서 알림을 다시 골라 주세요.";
  }
  if (isApiError(error, "validation_failed")) {
    return "이 값으로는 넣을 수 없어요. 고치기에서 날짜와 시간을 확인해 주세요.";
  }
  return "넣지 못했어요. 다시 누르면 이것만 다시 넣어요.";
}

/** commit 이 실패했을 때. 🚨 **넣은 일정이 있으면 그것부터 말한다** — "아무것도 저장 안 됐어요" 가 거짓이 된다. */
function commitText(failure: PhotoCommitFailure, added: number): string {
  const head =
    added > 0
      ? `읽은 내용을 저장하지 못했어요. 캘린더에 넣은 일정 ${added}개는 들어갔어요.`
      : "저장하지 못했어요. 아직 아무것도 저장되지 않았어요.";
  return failure === "expired"
    ? `${head} 읽은 지 오래돼서 이 사진으로는 더 저장할 수 없어요. 사진을 다시 올려 주세요.`
    : `${head} 다시 누르면 읽은 내용만 저장해요.`;
}

/** 넣을 일정의 일시. 🚨 넣을 값 그대로다 — 읽은 값이 아니라 보호자가 시트에서 고친 값이다. */
function planWhen(entry: PhotoEntry, plan: EventPlan): string {
  const startsAt = planStartsAt(entry, plan);
  if (startsAt === null) return "";
  return eventWhen(startsAt, planEndsAt(entry, plan, startsAt), plan.allDay);
}

/* ── 확인이 필요한 것 ─────────────────────────────────────────────────── */

interface GroupProps {
  entries: PhotoEntry[];
  added: Record<string, AddedEvent>;
  pickOf: (entry: PhotoEntry) => CalendarPick | null;
  busy: boolean;
  onPick: (id: string, next: boolean) => void;
  onEdit: (entry: PhotoEntry) => void;
}

/**
 * 🚨 **펼친 채로 맨 위에 둔다.** 부모가 이 화면에서 할 일은 "기계가 못 읽은 곳을 메우는 것"
 *    하나고, 그게 화면 아래에 있으면 스무 줄을 지나야 닿는다.
 */
function NeedsReviewGroup({ entries, ...row }: GroupProps) {
  if (entries.length === 0) return null;

  return (
    <section className="flex flex-col gap-2">
      <div>
        <h2 className="text-label text-brand">확인이 필요해요 {entries.length}건</h2>
        <p className="text-body-sm text-ink-muted mt-1">못 읽은 곳이 있어요. 고쳐야 저장돼요.</p>
      </div>
      <ul className="flex flex-col gap-2">
        {entries.map((entry) => (
          <li key={entry.id} data-flip={entry.id}>
            <EntryRow entry={entry} {...row} />
          </li>
        ))}
      </ul>
    </section>
  );
}

/* ── 잘 읽은 것 ───────────────────────────────────────────────────────── */

/**
 * 🚨 **기본이 접힘이다.** 식단표는 거의 한 달치라 스무 개가 넘는데, 잘 읽은 것을 전부 펼쳐 두면
 *    위의 "확인이 필요해요" 가 화면 밖으로 밀린다. 건수는 접힌 채로도 보인다.
 *    켜진 "캘린더에 넣기" 가 이 안에 있어도 무엇을 넣는지는 맨 아래 버튼 위에서 보인다 (#824).
 * 🚨 **펼친 줄은 투명도로만 선다** (디자인 시스템 §8 "펼침"). 높이를 움직이지 않는다 — 목록이 자라는
 *    동안 아래 저장 버튼이 같이 끌려 내려가며 흔들린다. 줄마다의 페이드는 위 `useFlip` 이 건다.
 */
function CheckedGroup({
  entries,
  open,
  onToggle,
  ...row
}: GroupProps & { open: boolean; onToggle: () => void }) {
  if (entries.length === 0) return null;

  /**
   * 몇 줄부터 스크롤을 거는가. 🚨 **짧은 목록에까지 걸지 않는다** — 알림장 서너 줄에 상자가
   * 생기면 잘린 것처럼 보이고, 스크롤할 것도 없는데 스크롤 영역이 하나 더 생긴다.
   */
  const scrollable = entries.length > 6;

  return (
    <section className="flex flex-col gap-2">
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={open}
        aria-controls="photo-checked-list"
        className="min-h-touch flex w-full items-center justify-between gap-2 text-left"
      >
        <span>
          <span className="text-label text-brand block">잘 읽었어요 {entries.length}건</span>
          <span className="text-body-sm text-ink-muted mt-1 block">
            {open ? "고칠 것이 있으면 눌러서 바꿔요." : "눌러서 하나씩 확인할 수 있어요."}
          </span>
        </span>
        {/* 🚨 방향은 아이콘을 갈아 끼워 말한다 — 회전 애니메이션을 만들지 않는다 (`Select` 와 같다). */}
        {open ? (
          <ChevronUp
            aria-hidden
            size={ICON_SIZE.md}
            strokeWidth={ICON_STROKE}
            className="text-ink-subtle shrink-0"
          />
        ) : (
          <ChevronDown
            aria-hidden
            size={ICON_SIZE.md}
            strokeWidth={ICON_STROKE}
            className="text-ink-subtle shrink-0"
          />
        )}
      </button>

      {open ? (
        <ul
          id="photo-checked-list"
          className={cn(
            "flex flex-col gap-2",
            /**
             * 🚨 **펼쳐도 화면을 밀지 않게 하는 장치다.** 식단표 한 장이 스무 줄이 넘는데
             *    전부 쌓이면 아래의 "이렇게 저장할게요" 와 저장 버튼이 스크롤 끝으로 밀린다 —
             *    다 확인하고도 버튼을 찾아 한참 내려가야 한다 (11-2 검사지와 같은 값·같은 이유).
             * 🚨 **높이를 `rem` 으로 고정하지 않는다** — 줄이 글자 크기를 따라 늘어나는데
             *    상자만 고정이면 200% 확대에서 한 줄만 보인다 (디자인 시스템 §10).
             * 🚨 `overscroll-contain` 으로 끝까지 굴렸을 때 페이지가 따라 튀지 않게 한다.
             * 🚨 **확인이 필요해요 쪽에는 걸지 않는다** — 실제로 손봐야 하는 목록이라 가리면 안 된다.
             */
            scrollable ? "max-h-[60vh] overflow-y-auto overscroll-contain" : null,
          )}
        >
          {entries.map((entry) => (
            <li key={entry.id} data-flip={entry.id}>
              <EntryRow entry={entry} {...row} />
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}

/* ── 항목 한 줄 ───────────────────────────────────────────────────────── */

function EntryRow({
  entry,
  added: addedMap,
  pickOf,
  busy,
  onPick,
  onEdit,
}: Omit<GroupProps, "entries"> & { entry: PhotoEntry }) {
  /** 이 줄을 캘린더에 넣었다면 **넣은 일정.** 안 넣었으면 `undefined` 다. */
  const added = addedMap[entry.id];
  const pick = pickOf(entry);
  /**
   * 지난 일정이라 꺼 둔 줄은 왜 꺼졌는지 그 줄이 말한다 (#828 리뷰). 다른 일정은 켜져 있는데 이 줄만
   * 꺼져 있으면 못 읽은 것처럼 보인다. 🚨 켜면 이 말은 사라진다 — 넣지 말라는 것이 아니다.
   */
  const pastHint = pick?.pastOff && !pick.checked ? "지난 일정이라 꺼 두었어요." : undefined;

  return (
    <div
      className={cn(
        "rounded-card bg-surface ease-standard border p-4 transition-colors duration-120",
        // 🚨 확인이 필요한 것을 빨강으로 칠하지 않는다. 사고가 아니라 **아직 안 한 일**이다 —
        //    테두리만 진하게 해서 눈에 먼저 들어오게 한다.
        entry.needs_review ? "border-line-strong" : "border-line",
      )}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <p className="text-caption text-ink-subtle">{ENTRY_KIND_LABEL[entry.kind]}</p>
            {/* 🚨 **넣은 줄은 목록에서 바로 갈린다.** 시트를 열어야만 알 수 있으면, 스무 줄짜리
                알림장에서 부모는 어디까지 넣었는지 세지 못한다.
                🚨 **색으로만 가르지 않는다** — 칩이 글자를 지고 있어서 색 없이도 읽힌다
                (디자인 시스템 §3). 일정 초안 카드의 "넣었어요" 칩과 같은 모양·같은 뜻이다. */}
            {added ? (
              <span className="text-caption text-brand-ink bg-brand-soft rounded-field px-2 py-0.5">
                캘린더에 넣었어요
              </span>
            ) : null}
          </div>
          <p className="text-body text-ink mt-0.5">{entry.title}</p>
        </div>
        {/* 🚨 **고치기만 한다** (#824). 한동안 "고치고 일정 넣기" 하나에 두 일이 섞여 누르기 전에는
            무엇이 되는지 안 보였다. 캘린더에 넣을지는 아래 체크가 말한다. */}
        <Button variant="tertiary" size="compact" disabled={busy} onClick={() => onEdit(entry)}>
          고치기
        </Button>
      </div>

      <dl className="mt-3 flex flex-col gap-1">
        {/* 🚨 **날짜가 없는 안내에는 날짜 줄을 세우지 않는다** (#723). 안내는 날짜가 없는 것이
            보통인데 "못 읽었어요" 라고 쓰면 보호자가 없던 날짜를 고치러 들어간다.
            🚨 **할 일의 날짜는 "마감" 이다** — "언제" 로 부르면 그 날에 하는 일로 읽힌다.
               급식은 "마감" 이 아니다 (`entryDateLabel` · #792 리뷰). */}
        {entry.kind === "info" && entry.date === null ? null : (
          <EntryMeta
            term={entryDateLabel(entry)}
            value={entryWhen(entry)}
            missing={entry.date === null}
          />
        )}
        {/* 🚨 **기록의 일자와 다른 줄에 둔다.** 위는 아직 저장 안 한 기록의 값이고 이건 이미
            캘린더에 들어간 값이다 — 한 줄에 합치면 저장하지도 않은 것이 들어간 것처럼 읽힌다. */}
        {added ? <EntryMeta term="캘린더" value={calendarValue(entry, added)} /> : null}
        {entry.items.length > 0 ? <EntryMeta term="준비물" value={entry.items.join(", ")} /> : null}
      </dl>

      {/* 🚨 왜 확인이 필요한지는 **서버가 만든 문구**다. 프론트가 추측해 쓰지 않는다. */}
      {entry.needs_review && entry.review_reason ? (
        <p className="text-body-sm text-ink-muted bg-surface-muted rounded-field mt-3 px-3 py-2">
          {entry.review_reason}
        </p>
      ) : null}

      {/* 🚨 **캘린더에 넣을지는 줄마다 고른다** (#824). 켜도 넣은 것이 아니다 — 맨 아래 버튼을 눌러야 들어간다.
          🚨 **급식 · 안내에는 세우지 않는다** (`canBecomeEvent`) · 이미 넣은 줄도 세우지 않는다 — 끈다고
             캘린더에서 빠지지 않는다. 그 줄은 위의 칩과 "캘린더" 줄이 말한다.
          🚨 **잠긴 까닭을 그 자리에서 말한다** — 고치기에서 무엇을 채우면 되는지까지. */}
      {pick ? (
        <div className="border-line mt-3 border-t pt-1">
          <Checkbox
            checked={pick.checked}
            disabled={busy || pick.locks.length > 0}
            onChange={(next) => onPick(entry.id, next)}
            label="캘린더에 넣기"
            description={pick.failure ?? (pick.locks.length > 0 ? pick.locks.join(" ") : pastHint)}
          />
        </div>
      ) : null}
    </div>
  );
}

/**
 * 캘린더 줄에 뭐라고 쓰나.
 *
 * 🚨 **바로 위 칸이 이미 말한 날짜를 다시 쓰지 않는다** — 같은 날이면 시각만 낸다
 *    (`formatTimeOfDay` 가 있는 이유 그대로다 · `lib/format.ts` 머리말).
 * 🚨 **다르면 날짜까지 쓴다.** 기록의 일자를 나중에 고치면 캘린더에 들어간 날과 갈릴 수 있고,
 *    그때 시각만 보여주면 **다른 날에 들어간 일정**을 같은 날인 것처럼 읽게 된다.
 */
function calendarValue(entry: PhotoEntry, added: AddedEvent): string {
  const from = seoulDate(added.starts_at);
  const to = seoulDate(added.ends_at);
  // 여러 날이면 기간이 본체다. 위 칸과 같은 날에 시작해도 끝날까지 말한다.
  if (to !== "" && to !== from) return formatDayRange(from, to);
  // 🚨 **마감 줄과는 겹쳐 쓰지 않는다** — 위 칸이 마감이면 같은 날이라도 날짜까지 쓴다.
  //    시각만 내면 "마감 11월 14일" 아래의 "오전 9:00" 이 마감 시각으로 읽힌다.
  const sameDay = entryDateRole(entry) === "start" && entry.date !== null && entry.date === from;
  if (!sameDay) return formatEventTime(added.starts_at, added.all_day);
  return added.all_day ? "하루 종일" : formatTimeOfDay(added.starts_at);
}

/**
 * 날짜 줄에 뭐라고 쓰나. 서버가 여러 날을 읽었으면 기간을 쓴다 (#723 · `readSpan`).
 * 🚨 보호자가 시작일을 고쳤으면 기간도 시각도 쓰지 않는다 — 저장되는 것은 그날 하루다.
 * 🚨 **읽은 시각은 오전 · 오후와 함께 쓴다** (`readMoment` · #723 댓글). "8시" 를 오전으로 읽은 것이
 *    맞는지 보호자가 목록에서 본다.
 */
function entryWhen(entry: PhotoEntry): string {
  if (entry.date === null) return "못 읽었어요";
  const span = readSpan(entry);
  const moment = readMoment(entry);
  if (span)
    return moment
      ? `${formatDateTime(moment)}부터 ${formatDay(span.to)}까지`
      : formatDayRange(span.from, span.to);
  return moment ? formatDateTime(moment) : formatDay(entry.date);
}

function EntryMeta({
  term,
  value,
  missing = false,
}: {
  term: string;
  value: string;
  missing?: boolean;
}) {
  return (
    <div className="flex gap-3">
      <dt className="text-body-sm text-ink-subtle shrink-0 basis-16">{term}</dt>
      {/* 🚨 못 읽은 값도 **글자로** 말한다. 빈칸으로 두면 부모가 없는 건지 못 읽은 건지 모른다. */}
      <dd className={cn("text-body-sm min-w-0", missing ? "text-ink-muted" : "text-ink")}>
        {value}
      </dd>
    </div>
  );
}

/* ── 활동 lane — 태그 고르기 ──────────────────────────────────────────── */

/**
 * 🚨 **하나도 고르지 않은 채로 시작한다.** 활동 태그는 모델이 **아이에 대해 추측한 것**이라
 *    부모가 눌러서 켜야 한다 — 한 번의 관찰을 성향으로 확정하지 않는다는 규칙(CLAUDE.md §2)이
 *    화면에서 지켜지려면 아이에 대한 말은 확인을 거쳐야 한다.
 */
function TagPicker({
  tags,
  selected,
  onChange,
  busy,
}: {
  tags: string[];
  selected: string[];
  onChange: (next: string[]) => void;
  busy: boolean;
}) {
  return (
    <section className="flex flex-col gap-2">
      <div>
        <h2 className="text-label text-brand">뽑아낸 활동 태그</h2>
        <p className="text-body-sm text-ink-muted mt-1">
          맞는 것만 골라 주세요. 고르지 않은 태그는 저장하지 않아요.
        </p>
      </div>
      {tags.length > 0 ? (
        <ChipRow>
          {tags.map((tag) => (
            <Chip
              key={tag}
              selected={selected.includes(tag)}
              disabled={busy}
              onClick={() =>
                onChange(
                  selected.includes(tag) ? selected.filter((v) => v !== tag) : [...selected, tag],
                )
              }
            >
              {tag}
            </Chip>
          ))}
        </ChipRow>
      ) : (
        // 🚨 빈 배열을 숨기지 않는다. 못 읽었으면 못 읽었다고 말한다.
        <p className="text-body-sm text-ink-muted">이 사진에서는 활동 태그를 읽어내지 못했어요.</p>
      )}
    </section>
  );
}

function PreviewRow({ term, value }: { term: string; value: ReactNode }) {
  return (
    // 🚨 **글자를 키우면 값이 라벨 아래로 내려간다** (`flex-wrap` + 값의 최소 폭). "캘린더" 줄은 여러 줄짜리
    //    목록이라, 200% 에서 라벨 옆에 두면 한 줄에 한두 낱말씩 쪼개지고 카드 밖으로 삐져나갔다 (디자인 시스템 §10).
    <div className="flex flex-wrap gap-x-3 gap-y-1">
      {/* 🚨 폭을 고정하지 않는다. 글자를 키우면 라벨이 잘려서 무엇을 저장하는지가 가려진다. */}
      <dt className="text-body-sm text-ink-subtle shrink-0 basis-24">{term}</dt>
      <dd className="text-body-sm text-ink min-w-0 grow basis-48 break-words">{value}</dd>
    </div>
  );
}
