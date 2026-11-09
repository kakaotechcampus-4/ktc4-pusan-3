"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { X } from "lucide-react";
import { useState } from "react";

import { ReminderField } from "@/components/reminder-field";
import { BottomSheet } from "@/components/ui/bottom-sheet";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { DateField } from "@/components/ui/date-field";
import { ICON_SIZE, ICON_STROKE } from "@/components/ui/icon";
import { Spinner } from "@/components/ui/spinner";
import { TextInput } from "@/components/ui/text-input";
import { TimeField } from "@/components/ui/time-field";
import { qk, submitEventDraft } from "@/lib/api";
import type { PhotoEntry, SubmitEventBody } from "@/lib/api/types";
import { useIdempotencyKey } from "@/lib/api/use-idempotency-key";
import { cn } from "@/lib/cn";
import { seoulDate, withSeoulDateTime } from "@/lib/event-draft";
import { formatDayRange, formatEventTime, formatTimeOfDay } from "@/lib/format";
import {
  canBecomeEvent,
  carriedEndsAt,
  droppedEnd,
  entryDateLabel,
  entryDateRole,
  readStartTime,
} from "@/lib/photo-entry";
import { forKind, isPast, NO_REMINDER, type ReminderChoice } from "@/lib/reminder";

/**
 * 08 사진 — 읽어낸 항목 **하나**를 고치는 시트.
 *
 * 🚨 **여기서 고친 값이 저장되는 값이다.** 모델이 읽은 것을 부모가 덮어쓰는 유일한 자리고,
 *    확인을 누른 항목만 `commit` 에 실린다 (`photo-review.tsx`).
 *
 * ## 🚨 이 시트 **안에** 승인 게이트 ㉠ 이 하나 있다
 *
 * "일정으로도 넣기" 를 켜고 **일정 넣기**를 누르면 그 자리에서 캘린더에 쓴다
 * (`POST /children/{cid}/events`). 알림장 한 장에서 일정이 여러 건 나오는데, 고치는 자리와
 * 일정으로 만드는 자리가 갈려 있으면 **같은 항목을 두 화면에서 두 번 확인**하게 된다 —
 * 무엇을 넣는지 아는 자리가 바로 여기다.
 *
 * 🚨 **그래서 그 버튼만 `btn-approve` + `caution` 이다.** 아래 "이 내용으로 확인" 은 게이트가
 *    아니라 목록에 반영하는 것이라 그냥 primary 다 — 둘을 같은 색으로 두면 무엇이 되돌릴 수
 *    없는지가 사라진다.
 *
 * 🚨 **일정과 기록은 따로 간다.** 일정은 이 버튼이 그 자리에서 넣고, 기록(관찰)은 화면 맨 아래
 *    "이 내용으로 저장" 이 여러 건을 한 번에 보낸다. 하나를 눌렀다고 다른 하나가 되지 않는다.
 *
 * 🚨 **화면 하나로 만들지 않는다.** 알림장 한 장에 항목이 여러 개라 고칠 때마다 화면을 옮기면
 *    목록에서의 자리를 잃는다 — 시트는 덮고 닫히면 있던 자리로 돌아온다.
 *
 * ⚠️ **시트 안에 시트가 하나 더 열린다** (`DateField` 의 달력). 네이티브 `<dialog>` 는 top layer
 *    를 쌓으므로 달력이 위에 뜨고, 닫히면 이 시트로 포커스가 돌아온다.
 */
export function PhotoEntrySheet({
  entry,
  childId,
  added,
  onEventAdded,
  onClose,
  onSave,
  onRemove,
}: {
  /** `null` 이면 닫혀 있다. 열 때마다 새로 세우려고 호출부가 `key` 를 준다. */
  entry: PhotoEntry | null;
  childId: string;
  /**
   * 이 항목으로 **이미 캘린더에 넣은 것.** 안 넣었으면 `undefined` 다. 🚨 두 번 넣는 길을 막는다.
   * 🚨 **넣은 일시를 그대로 다시 말한다** — 할 일은 위 칸이 마감이라, 위 칸의 날짜로 "넣었어요"
   *    를 말하면 들어가지도 않은 날을 들어갔다고 한다 (#723).
   */
  added: AddedEventInput | undefined;
  /**
   * 넣고 나서 호출부에 알린다 — 목록·요약이 "일정 N건 넣음" 을 말해야 한다.
   *
   * 🚨 **무엇이 들어갔는지 함께 올린다.** id 만 넘기면 목록은 "넣었다" 까지만 말할 수 있고,
   *    **언제로** 넣었는지는 이 시트를 닫는 순간 사라진다 — 방금 고른 일시를 확인하려고
   *    캘린더까지 가야 한다.
   */
  onEventAdded: (entryId: string, added: AddedEventInput) => void;
  onClose: () => void;
  /** 고친 값. 호출부가 목록의 그 항목을 갈아 끼우고 **확인됨**으로 표시한다. */
  onSave: (next: PhotoEntry) => void;
  /** 이 항목을 저장에서 뺀다. */
  onRemove: (id: string) => void;
}) {
  return (
    <BottomSheet
      open={entry !== null}
      onClose={onClose}
      /**
       * 🚨 **제목이 이 시트에서 할 수 있는 일을 다 말한다.** "고치기" 만 적어 두니 캘린더에
       *    넣는 일이 이 안에 있다는 것이 어디에도 안 보였다 — 부모는 열어 보고 나서야 안다.
       * 🚨 급식 · 안내는 예외다 — 일정 칸을 아예 세우지 않는 종류라(`canBecomeEvent`) 제목이
       *    없는 기능을 약속하면 안 된다.
       */
      title={entry && canBecomeEvent(entry) ? "고치고 일정 넣기" : "읽은 내용 고치기"}
      description="고친 값은 마지막에 저장을 누를 때 함께 저장돼요."
    >
      {/* 🚨 열 때마다 처음 값에서 시작한다 — 앞 항목을 고치던 값이 남으면 다른 항목에 덮인다. */}
      {entry ? (
        <EntryForm
          key={entry.id}
          entry={entry}
          childId={childId}
          added={added}
          onEventAdded={onEventAdded}
          onSave={onSave}
          onRemove={onRemove}
        />
      ) : null}
    </BottomSheet>
  );
}

/**
 * 캘린더에 넣으면서 호출부에 올리는 것.
 *
 * 🚨 **넣은 일시만이 아니라 확인한 값 전체를 올린다.** 부모가 승인 게이트를 지난 값인데
 *    목록이 계속 "못 읽었어요 · 고쳐야 저장돼요" 라고 하면, 화면이 **방금 부모가 확인한 것을
 *    못 본 척**한다 (실제로 그렇게 보였다 — 캘린더 줄에는 날짜가 서 있는데 바로 위에서
 *    날짜를 못 읽었다고 했다).
 * 🚨 **저장(관찰)까지 하는 것은 아니다.** 기록은 여전히 화면 맨 아래 버튼이 한 번에 보낸다 —
 *    여기서 옮기는 것은 "이 값으로 확인했다" 까지다.
 */
export type AddedEventInput = {
  /**
   * 넣은 일정의 id. 🚨 **저장할 때 그 항목의 `event_id` 로 실어 보낸다** — 그래야 서버가 그 일정에
   *    이 공지 항목을 출처로 단다 (`photos.py` 의 commit). 안 실으면 일정과 공지가 따로 논다.
   */
  event_id: string;
  starts_at: string;
  /** 함께 넣은 끝. 읽은 기간을 그대로 넣었을 때만 있다 (`carriedEndsAt`). */
  ends_at: string | null;
  all_day: boolean;
  title: string;
  /**
   * 🚨 **위 칸의 날짜다** — 일정이 들어간 날이 아니다. 할 일은 이 칸이 마감이라 일정의 시작과
   *    다른 날일 수 있다. 일정이 들어간 날은 `starts_at` 이 말한다.
   */
  date: string;
  items: string[];
};

/** 고를 수 있는 날의 범위. 🚨 알림장은 **다음 주 일정**을 싣기 때문에 미래를 막지 않는다. */
const FROM_DATE = new Date(2020, 0, 1);
const TO_DATE = new Date(new Date().getFullYear() + 2, 11, 31);

function EntryForm({
  entry,
  childId,
  added,
  onEventAdded,
  onSave,
  onRemove,
}: {
  entry: PhotoEntry;
  childId: string;
  added: AddedEventInput | undefined;
  onEventAdded: (entryId: string, added: AddedEventInput) => void;
  onSave: (next: PhotoEntry) => void;
  onRemove: (id: string) => void;
}) {
  const queryClient = useQueryClient();
  /** 위 칸이 시작인지 마감인지 (`lib/photo-entry.ts`). 🚨 저장할 때 서버가 고치는 쪽과 같다. */
  const role = entryDateRole(entry);
  const eventAdded = added !== undefined;
  const [title, setTitle] = useState(entry.title);
  const [date, setDate] = useState(entry.date ?? "");
  const [items, setItems] = useState(entry.items);
  const [adding, setAdding] = useState("");
  const [error, setError] = useState<string | null>(null);

  /**
   * 일정으로도 넣을 것인가. 🚨 **기본값은 꺼짐이다.** 알림장에서 날짜를 읽었다고 해서 그것이
   *    캘린더에 넣을 일정이라는 뜻은 아니다 — 급식표는 전부 날짜가 있지만 일정이 아니다.
   *    켜는 것은 보호자가 한다 (§3 — 되돌릴 수 없는 것은 사람이 정한다).
   */
  const [asEvent, setAsEvent] = useState(false);
  /**
   * 🚨 마감 항목의 `all_day` 는 **마감**이 하루 종일이라는 뜻이다(서버 `due_all_day`). 일정의
   *    시작과 상관없어서 쓰지 않고 하루 종일에서 시작한다.
   */
  const [allDay, setAllDay] = useState(role === "start" ? (entry.all_day ?? true) : true);
  /**
   * 🚨 **시각은 읽은 것이 있을 때만 채운다** (#723). 서버가 "9시 30분" 을 읽어 `starts_at` 에
   *    실어 보내는데 칸을 비워 열면, 보호자가 같은 시각을 다시 넣어야 일정을 넣을 수 있었다.
   * 🚨 **없으면 비어서 시작한다.** 없는 것을 자정으로 채우면 아무도 고르지 않은 "오전 12:00" 이
   *    캘린더에 들어간다 — 하루 종일을 끄면 실제로 그렇게 나가고 있었다 (#151 · `lib/event-draft.ts` 머리말).
   */
  const [time, setTime] = useState(() => readStartTime(entry));
  /**
   * 마감 항목을 일정으로 넣을 때의 **시작 날짜** (#723). 🚨 **비어서 시작한다** — 마감일을 시작일로
   *    채우면 "11/14 까지 회신" 이 11/14 에 시작하는 일정으로 들어간다. 시작 항목은 위 칸이 곧
   *    시작이라 이 칸을 쓰지 않는다.
   */
  const [startDate, setStartDate] = useState("");
  const eventDate = role === "start" ? date : startDate;
  /** 넣을 일정의 내 알림 (#609 · 초안 카드와 같은 칸). 🚨 기본값은 "없음" 이다. */
  const [reminder, setReminder] = useState<ReminderChoice>(NO_REMINDER);
  const startsAt = withSeoulDateTime(eventDate, time, allDay);
  /**
   * 🚨 **읽은 끝을 함께 넣는다** (#723). 한동안 `null` 로 보내서 "11/23 ~ 11/27" 이 첫날 하루짜리
   *    일정이 됐다. 시작을 고쳤으면 읽은 끝을 붙이지 않는다 (`carriedEndsAt`).
   */
  const endsAt = carriedEndsAt(entry, { date: eventDate, allDay, startsAt });
  /** 읽은 끝이 있었는데 보호자가 시작을 고쳐서 빠지게 된 경우. 조용히 줄이지 않고 말한다. */
  const dropped = droppedEnd(entry, { startsAt, endsAt });
  const endText = endsAt !== null && startsAt !== null ? endNote(startsAt, endsAt, allDay) : null;

  /** 🚨 한 항목이 사용자 동작 하나다. 재시도는 같은 키로 간다. */
  const eventKey = useIdempotencyKey();

  /** 급식은 준비물을 갖지 않는다 (위 🚨). */
  const showItems = entry.kind !== "meal";
  /** 일정 칸을 세우는가. 🚨 급식 · 안내는 세우지 않는다 (`canBecomeEvent`). */
  const showEvent = canBecomeEvent(entry);

  function addItem() {
    const next = adding.trim();
    if (next.length === 0) return;
    // 같은 것을 두 번 넣지 않는다. 조용히 무시하지 말고 입력만 비운다.
    if (!items.includes(next)) setItems([...items, next]);
    setAdding("");
  }

  /**
   * 🚨 **승인 게이트 ㉠.** 여기서 캘린더에 쓴다 — 초안이 아니라 확정이다.
   * 🚨 **여기서 보내는 것은 화면에 보이는 값**이다. 저장 버튼을 아직 안 눌렀어도 상관없다 —
   *    일정과 기록은 서로 다른 것이고, 각자의 버튼이 각자를 확정한다.
   */
  const addEvent = useMutation({
    /**
     * 🚨 **보낸 시각을 뮤테이션 변수로 들고 간다.** `onSuccess` 에서 다시 조립하면 그 사이
     *    칸을 만진 값이 잡혀서, **캘린더에 들어간 것과 목록이 말하는 것이 달라진다.**
     */
    mutationFn: ({ startsAt, endsAt }: { startsAt: string; endsAt: string | null }) => {
      const body: SubmitEventBody = {
        event: {
          title: title.trim(),
          starts_at: startsAt,
          ends_at: endsAt,
          all_day: allDay,
          event_type: "episodic",
          // 🚨 알림장에서 읽은 것은 기관이 알려준 일정이다 (계약서 §09 와 같은 출처 판정).
          category: "institution",
        },
        items: showItems ? items.map((item) => ({ item_id: null, item_name: item })) : [],
        reminder: reminder === NO_REMINDER ? null : { lead: reminder },
      };
      return submitEventDraft(childId, body, eventKey.current());
    },
    onSuccess: async (res) => {
      // 🚨 성공한 뒤에만 다음 키로 넘어간다. 실패 뒤 다시 누르는 것은 재시도라 같은 키여야 한다.
      eventKey.rotate();
      onEventAdded(entry.id, {
        event_id: res.event.id,
        /**
         * 🚨 **일시는 응답에서 받는다** (#792 리뷰). 응답을 못 받은 뒤 칸을 고쳐 다시 누르면 같은 키라
         *    서버는 **처음 넣은 일정**을 돌려준다. 보낸 값을 쓰면 목록이 들어가지 않은 일시를 말한다.
         */
        starts_at: res.event.starts_at,
        ends_at: res.event.ends_at,
        all_day: res.event.all_day,
        title: title.trim(),
        date,
        // 🚨 급식은 준비물 칸 자체가 없으니 원래 값을 그대로 둔다 (`submit` 과 같은 규칙).
        items: showItems ? items : entry.items,
      });
      await queryClient.invalidateQueries({ queryKey: qk.child(childId) });
    },
  });

  /** 🚨 잠금이 그대로면 버튼이 비활성이라 여기 못 온다 — 타입을 좁히는 자리다. */
  function addToCalendar() {
    if (startsAt !== null) addEvent.mutate({ startsAt, endsAt });
  }

  /**
   * 🚨 제목과 일시가 없으면 넣을 수 없다. 화면이 **왜** 잠겼는지 말한다.
   *
   * 🚨 **막는 것이 여럿이면 전부 말한다** (초안 카드와 같은 규칙). 한동안 첫 번째 것만 말했는데,
   *    시각 칸이 서면서 알림장에서 온 항목은 **날짜도 시각도** 비어 있게 됐다 — 하나씩 말하면
   *    보호자가 날짜를 채운 뒤에야 두 번째 관문을 알게 된다.
   */
  const eventLocks = [
    title.trim().length === 0 ? "무엇인지 적어주셔야 넣을 수 있어요." : null,
    eventDate === ""
      ? role === "start"
        ? "날짜를 골라주셔야 넣을 수 있어요."
        : "시작하는 날을 골라주셔야 넣을 수 있어요."
      : null,
    // 🚨 하루 종일이면 시각을 안 묻는다 — 물을 것이 없는 것이지 안 고른 것이 아니다.
    !allDay && time === "" ? "몇 시에 시작하는지 골라주셔야 넣을 수 있어요." : null,
    // 🚨 조용히 "없음" 으로 바꾸지 않는다 — 고른 것과 들어간 것이 달라진다 (초안 카드와 같다).
    reminder !== NO_REMINDER && startsAt !== null && isPast(reminder, startsAt, allDay)
      ? "고른 알림 시각이 이미 지났어요. 알림을 다시 골라주셔야 넣을 수 있어요."
      : null,
  ].filter((reason) => reason !== null);

  function submit() {
    if (title.trim().length === 0) {
      setError("무엇인지 한 줄로 적어주세요.");
      return;
    }
    onSave({
      ...entry,
      title: title.trim(),
      date: date === "" ? null : date,
      // 🚨 급식은 준비물 칸 자체가 없으니 원래 값을 그대로 둔다 (빈 배열로 덮지 않는다).
      items: showItems ? items : entry.items,
      // 🚨 부모가 확인했으므로 더 이상 "확인이 필요한" 항목이 아니다. 이 전환이 저장 대상을 만든다.
      needs_review: false,
      review_reason: undefined,
    });
  }

  return (
    <div className="flex flex-col gap-4">
      <TextInput
        label="무엇인가요"
        value={title}
        error={error}
        onChange={(e) => {
          setTitle(e.target.value);
          if (error) setError(null);
        }}
      />

      {/* 🚨 날짜는 비워 둘 수 있다. 읽어내지 못한 것을 오늘로 채우지 않는다.
          🚨 **"일정으로 올리지 않아요" 라고 쓰지 않는다** — 일정은 이제 아래 체크박스로 고르는
             것이지 날짜가 정하는 것이 아니다. 날짜는 일정을 넣을 때 **필요한 값**일 뿐이다. */}
      {/* 🚨 **할 일의 날짜는 마감이다** (#723). "언제" 로 부르면 보호자는 그 날을 일정의 날로 읽고,
          서버는 이 칸을 고치면 마감을 고친다 (`lib/photo-entry.ts`). */}
      <DateField
        label={entryDateLabel(entry)}
        hint={dateHint(entry, role)}
        value={date}
        onChange={setDate}
        fromDate={FROM_DATE}
        toDate={TO_DATE}
      />

      {/* 🚨 **급식에는 준비물이 없다.** 급식표에서 읽은 칸에 준비물 입력을 세우면 화면이
          "여기 뭔가 적어야 하나" 를 묻는 셈이 된다 — 없는 항목을 빈칸으로 보여주지 않는다.
          일정·준비물 항목에만 선다. */}
      {showItems ? (
        <div className="flex flex-col gap-2">
          <span className="text-label text-ink-muted">준비물</span>
          {items.length > 0 ? (
            <ul className="flex flex-wrap gap-2">
              {items.map((item) => (
                <li key={item}>
                  {/* 🚨 `Chip` 을 쓰지 않는다 — 저 칩은 **고르는** 것이고 여기는 **지우는** 것이다.
                    같은 모양이면 눌렀을 때 켜지는 줄 안다. */}
                  <button
                    type="button"
                    onClick={() => setItems(items.filter((v) => v !== item))}
                    className="text-label min-h-chip border-line bg-surface text-ink-muted hover:border-line-strong active:border-line-strong ease-standard inline-flex items-center gap-1 rounded-full border px-2.5 py-0.5 transition-colors duration-120"
                  >
                    {item}
                    <X aria-hidden size={ICON_SIZE.sm} strokeWidth={ICON_STROKE} />
                    <span className="sr-only">빼기</span>
                  </button>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-body-sm text-ink-muted">아직 없어요.</p>
          )}

          <div className="flex items-end gap-2">
            <div className="min-w-0 flex-1">
              <TextInput
                label="준비물 더하기"
                value={adding}
                placeholder="예: 물병"
                onChange={(e) => setAdding(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key !== "Enter") return;
                  // 🚨 시트 안이라 Enter 가 바깥 폼을 제출하지 않게 막는다.
                  e.preventDefault();
                  addItem();
                }}
              />
            </div>
            <Button variant="secondary" disabled={adding.trim().length === 0} onClick={addItem}>
              더하기
            </Button>
          </div>
        </div>
      ) : null}

      {/* ── 🚨 승인 게이트 ㉠ — 여기서 캘린더에 쓴다 ──────────────────────────
          🚨 **급식에는 세우지 않는다.** 식단표는 전부 날짜가 있지만 일정이 아니다 —
             칸을 세우면 한 달치 급식마다 "일정으로 넣을까" 를 묻게 된다.
          🚨 **안내에도 세우지 않는다** (#723 · `canBecomeEvent`). */}
      {showEvent ? (
        <div className="border-line rounded-card border p-4">
          <Checkbox
            checked={eventAdded || asEvent}
            // 🚨 이미 넣은 것은 끌 수 없다 — 끈다고 캘린더에서 빠지지 않는다.
            onChange={(next) => !eventAdded && setAsEvent(next)}
            label="일정으로도 넣기"
            description="켜면 캘린더에 넣을 날짜와 시간을 고를 수 있어요. 읽은 내용 저장과는 따로예요."
          />

          {added ? (
            <p role="status" className="text-body-sm text-ink-muted mt-2">
              캘린더에 넣었어요. {addedWhen(added)}. 함께 보는 보호자에게도 보여요.
            </p>
          ) : asEvent ? (
            <div className="mt-3 flex flex-col gap-3">
              {/* 🚨 마감 항목만 시작 날짜를 따로 묻는다. 위 칸은 마감이라 그 날로 넣지 않는다. */}
              {role === "due" ? (
                <DateField
                  label="시작하는 날"
                  hint={date ? "마감은 위에 적힌 날이에요. 일정은 이 날에 들어가요." : undefined}
                  value={startDate}
                  onChange={setStartDate}
                  fromDate={FROM_DATE}
                  toDate={TO_DATE}
                />
              ) : null}

              <Checkbox
                checked={allDay}
                onChange={(next) => {
                  setAllDay(next);
                  // 🚨 지우지 않고 날 수를 지켜 옮긴다 — 서버 `for_kind` 와 같은 방향이다.
                  setReminder((prev) => forKind(prev, next));
                }}
                label="하루 종일"
              />

              {/* 🚨 **하루 종일이면 칸을 감춘다** (초안 카드와 같은 처리). 읽은 시각이 있으면 채워서
                  열고(`readStartTime`), 없으면 비어서 연다 — 고르기 전에는 아래 버튼이 잠기고,
                  왜 잠겼는지는 버튼 위에서 말한다. */}
              {!allDay ? <TimeField label="시작 시간" value={time} onChange={setTime} /> : null}

              {/* 🚨 **함께 넣는 끝을 게이트 앞에서 보여준다.** 보이지 않는 값이 캘린더에 들어가면
                  보호자가 확인한 것과 들어간 것이 다르다. 고쳐서 하루로 줄었을 때도 말한다. */}
              {endText !== null ? (
                <p className="text-body-sm text-ink-muted">{endText}</p>
              ) : dropped === "span" ? (
                <p className="text-body-sm text-ink-muted">
                  날짜나 하루 종일을 바꿔서 읽은 기간 대신 그날 하루로 넣어요.
                </p>
              ) : dropped === "time" ? (
                <p className="text-body-sm text-ink-muted">
                  시작 시간이나 하루 종일을 바꿔서, 읽어 둔 끝나는 시각은 빼고 넣어요.
                </p>
              ) : null}

              <ReminderField
                startsAt={startsAt}
                allDay={allDay}
                value={reminder}
                onChange={setReminder}
              />

              {addEvent.isError ? (
                // 🚨 실패를 빨강으로 칠하지 않는다 (디자인 시스템 §3).
                <p
                  role="status"
                  className="bg-surface-muted rounded-field text-body-sm text-ink-muted px-3 py-2"
                >
                  넣지 못했어요. 아직 아무것도 넣지 않았어요.
                </p>
              ) : null}

              {/* 🚨 **게이트의 마지막 확인은 버튼 옆에 선다** (`safety-scan-review.tsx` 선례).
                  배너를 하나 더 세우지 않는다 — 색은 `caution` 을 쓰되 글자 한 덩이다. */}
              <div
                className={cn(
                  "text-body-sm ease-standard flex flex-col gap-1 transition-colors duration-120",
                  eventLocks.length > 0 ? "text-ink-subtle" : "text-caution",
                )}
              >
                {eventLocks.length > 0 ? (
                  eventLocks.map((reason) => <p key={reason}>{reason}</p>)
                ) : (
                  <p>누르면 캘린더에 바로 들어가요. 아래 저장과는 따로예요.</p>
                )}
              </div>

              <Button
                variant="approve"
                disabled={eventLocks.length > 0 || addEvent.isPending}
                aria-busy={addEvent.isPending}
                onClick={addToCalendar}
              >
                {addEvent.isPending ? <Spinner /> : null}
                {addEvent.isPending ? "넣는 중이에요" : "확인했어요, 캘린더에 넣을게요"}
              </Button>
            </div>
          ) : null}
        </div>
      ) : null}

      <div className="flex flex-col gap-2">
        <Button block onClick={submit}>
          이 내용으로 확인
        </Button>
        {/* 🚨 `btn-danger` 가 아니다 — 아직 저장된 것이 없어서 지우는 것이 아니라 **안 싣는** 것이다.
            빨강은 알레르기·건강 중단에만 쓴다 (디자인 시스템 §3). */}
        <Button variant="tertiary" block onClick={() => onRemove(entry.id)}>
          이 항목은 저장하지 않기
        </Button>
      </div>
    </div>
  );
}

/** 위 날짜 칸의 도움말. 🚨 종류마다 "비워도 되는가" 와 "일정에 쓰이는가" 가 다르다. */
function dateHint(entry: PhotoEntry, role: "start" | "due"): string {
  // 안내는 날짜가 없는 것이 보통이다 (#723). "못 읽었으면" 이라고 하면 없던 날을 찾으러 간다.
  if (!canBecomeEvent(entry)) {
    return entry.kind === "info"
      ? "날짜가 없는 안내면 비워 두세요."
      : "못 읽었으면 비워 둬도 돼요.";
  }
  if (role === "due")
    return "못 읽었으면 비워 둬도 돼요. 일정으로 넣을 때는 시작하는 날을 따로 골라요.";
  return "못 읽었으면 비워 둬도 돼요. 일정으로 넣으려면 날짜가 있어야 해요.";
}

/**
 * 함께 넣는 끝 한 줄. 여러 날이면 기간을, 같은 날이면 끝나는 시각만 말한다.
 * 🚨 하루 종일 하루짜리의 끝(그날 23:59)은 말하지 않는다 — 서버 관례일 뿐 보호자가 고른 값이 아니다.
 */
function endNote(startsAt: string, endsAt: string, allDay: boolean): string | null {
  const from = startsAt.slice(0, 10);
  const to = seoulDate(endsAt);
  if (from !== to) return `${formatDayRange(from, to)} 함께 넣어요.`;
  return allDay ? null : `${formatTimeOfDay(endsAt)}에 끝나요.`;
}

/** 넣은 일정이 언제인지. 여러 날이면 기간이다. */
function addedWhen(added: AddedEventInput): string {
  const from = seoulDate(added.starts_at);
  const to = seoulDate(added.ends_at);
  if (to !== "" && from !== to) return formatDayRange(from, to);
  return formatEventTime(added.starts_at, added.all_day);
}
