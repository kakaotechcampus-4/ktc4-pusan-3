"use client";

import { X } from "lucide-react";
import { useState } from "react";

import { ReminderField } from "@/components/reminder-field";
import { BottomSheet } from "@/components/ui/bottom-sheet";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { DateField } from "@/components/ui/date-field";
import { ICON_SIZE, ICON_STROKE } from "@/components/ui/icon";
import { TextInput } from "@/components/ui/text-input";
import { TimeField } from "@/components/ui/time-field";
import type { PhotoEntry } from "@/lib/api/types";
import { seoulDate } from "@/lib/event-draft";
import { formatDayRange, formatTimeOfDay } from "@/lib/format";
import {
  canBecomeEvent,
  droppedEnd,
  entryDateLabel,
  entryDateRole,
  eventWhen,
  planEndsAt,
  planStartsAt,
  type AddedEvent,
  type EventPlan,
} from "@/lib/photo-entry";
import { forKind, NO_REMINDER, type ReminderChoice } from "@/lib/reminder";

/**
 * 08 사진 — 읽어낸 항목 **하나**를 고치는 시트.
 *
 * 🚨 **여기서 고친 값이 저장되는 값이다.** 모델이 읽은 것을 부모가 덮어쓰는 유일한 자리고,
 *    확인을 누른 항목만 `commit` 에 실린다 (`photo-review.tsx`).
 *
 * ## 🚨 여기서는 캘린더에 쓰지 않는다 (#824)
 *
 * 한동안 이 시트 안에 승인 게이트 ㉠ 이 있었다 — "일정으로도 넣기" 를 켜고 "캘린더에 넣을게요" 를
 * 누르면 그 자리에서 들어갔다. 일정이 셋이면 시트를 세 번 열었고, 버튼 하나("고치고 일정 넣기")에
 * 두 일이 섞여 누르기 전에는 무엇이 되는지 안 보였고, 저장이 두 군데라 헷갈렸다.
 *
 * 이제 넣을지는 **목록 줄의 체크**가, 넣는 것은 **맨 아래 버튼 하나**가 한다. 이 시트에는 넣을 때 쓰는
 * **값**만 남는다 — "캘린더에 넣을 때" 칸(시작하는 날 · 하루 종일 · 시작 시간 · 알림). 그 칸까지 빼면
 * 할 일의 시작하는 날을 고를 곳이 없어 마감일로 넣게 된다 (#723 에서 막은 것).
 *
 * 🚨 **그래서 게이트 표식(`btn-approve` · `caution`)이 여기 없다.** 그 둘은 맨 아래 버튼이 가져간다.
 *
 * 🚨 **화면 하나로 만들지 않는다.** 알림장 한 장에 항목이 여러 개라 고칠 때마다 화면을 옮기면
 *    목록에서의 자리를 잃는다 — 시트는 덮고 닫히면 있던 자리로 돌아온다.
 *
 * ⚠️ **시트 안에 시트가 하나 더 열린다** (`DateField` 의 달력). 네이티브 `<dialog>` 는 top layer
 *    를 쌓으므로 달력이 위에 뜨고, 닫히면 이 시트로 포커스가 돌아온다.
 */
export function PhotoEntrySheet({
  entry,
  plan,
  added,
  onClose,
  onSave,
  onRemove,
}: {
  /** `null` 이면 닫혀 있다. 열 때마다 새로 세우려고 호출부가 `key` 를 준다. */
  entry: PhotoEntry | null;
  /** 이 항목을 캘린더에 넣을 때의 값. 급식 · 안내는 쓰지 않는다. */
  plan: EventPlan | null;
  /**
   * 이 항목으로 **이미 캘린더에 넣은 것.** 안 넣었으면 `undefined` 다. 🚨 넣은 뒤에는 넣을 때의 값을
   * 고칠 칸을 감춘다 — 고쳐도 캘린더의 일정은 그대로라서, 칸이 서 있으면 고친 값이 들어간 줄 안다.
   */
  added: AddedEvent | undefined;
  onClose: () => void;
  /** 고친 값. 호출부가 목록의 그 항목을 갈아 끼우고 **확인됨**으로 표시한다. */
  onSave: (next: PhotoEntry, plan: EventPlan | null) => void;
  /** 이 항목을 저장에서 뺀다. */
  onRemove: (id: string) => void;
}) {
  return (
    <BottomSheet
      open={entry !== null}
      onClose={onClose}
      // 🚨 이 시트는 고치기만 한다 (위 주석). 제목이 캘린더를 약속하지 않는다.
      title="읽은 내용 고치기"
      description="고친 값은 마지막에 저장을 누를 때 함께 저장돼요."
    >
      {/* 🚨 열 때마다 처음 값에서 시작한다 — 앞 항목을 고치던 값이 남으면 다른 항목에 덮인다. */}
      {entry ? (
        <EntryForm
          key={entry.id}
          entry={entry}
          plan={plan}
          added={added}
          onSave={onSave}
          onRemove={onRemove}
        />
      ) : null}
    </BottomSheet>
  );
}

/** 고를 수 있는 날의 범위. 🚨 알림장은 **다음 주 일정**을 싣기 때문에 미래를 막지 않는다. */
const FROM_DATE = new Date(2020, 0, 1);
const TO_DATE = new Date(new Date().getFullYear() + 2, 11, 31);

function EntryForm({
  entry,
  plan,
  added,
  onSave,
  onRemove,
}: {
  entry: PhotoEntry;
  plan: EventPlan | null;
  added: AddedEvent | undefined;
  onSave: (next: PhotoEntry, plan: EventPlan | null) => void;
  onRemove: (id: string) => void;
}) {
  /** 위 칸이 시작인지 마감인지 (`lib/photo-entry.ts`). 🚨 저장할 때 서버가 고치는 쪽과 같다. */
  const role = entryDateRole(entry);
  const [title, setTitle] = useState(entry.title);
  const [date, setDate] = useState(entry.date ?? "");
  const [items, setItems] = useState(entry.items);
  const [adding, setAdding] = useState("");
  const [error, setError] = useState<string | null>(null);

  /** 급식은 준비물을 갖지 않는다 (아래 🚨). */
  const showItems = entry.kind !== "meal";
  /** "캘린더에 넣을 때" 칸을 세우는가. 🚨 급식 · 안내는 세우지 않는다 (`canBecomeEvent`). */
  const showEvent = canBecomeEvent(entry) && plan !== null;

  /**
   * 캘린더에 넣을 때의 값. 🚨 **확인을 눌러야 목록에 반영된다** — 위 칸들과 같다. 닫으면 버린다.
   * 처음 값은 호출부가 들고 있는 것이다 (`initialPlan` 에서 시작해 앞서 확인한 값).
   */
  const [startDate, setStartDate] = useState(plan?.startDate ?? "");
  const [allDay, setAllDay] = useState(plan?.allDay ?? true);
  const [time, setTime] = useState(plan?.time ?? "");
  const [reminder, setReminder] = useState<ReminderChoice>(plan?.reminder ?? NO_REMINDER);
  const draftPlan: EventPlan = { startDate, allDay, time, reminder };

  /**
   * 🚨 **지금 칸에 보이는 값으로** 미리 본다. 위 날짜 칸을 고쳤으면 고친 날이 시작이다 —
   *    목록에 반영된 날로 보면 시트가 들어가지도 않을 기간을 말한다.
   */
  const draftEntry: PhotoEntry = { ...entry, date: date === "" ? null : date };
  const startsAt = planStartsAt(draftEntry, draftPlan);
  /** 🚨 읽은 끝을 함께 넣는다 (#723). 시작을 고쳤으면 붙이지 않는다 (`carriedEndsAt`). */
  const endsAt = planEndsAt(draftEntry, draftPlan, startsAt);
  /** 읽은 끝이 있었는데 보호자가 시작을 고쳐서 빠지게 된 경우. 조용히 줄이지 않고 말한다. */
  const dropped = droppedEnd(entry, { startsAt, endsAt });
  const endText = endsAt !== null && startsAt !== null ? endNote(startsAt, endsAt, allDay) : null;

  function addItem() {
    const next = adding.trim();
    if (next.length === 0) return;
    // 같은 것을 두 번 넣지 않는다. 조용히 무시하지 말고 입력만 비운다.
    if (!items.includes(next)) setItems([...items, next]);
    setAdding("");
  }

  function submit() {
    if (title.trim().length === 0) {
      setError("무엇인지 한 줄로 적어주세요.");
      return;
    }
    onSave(
      {
        ...entry,
        title: title.trim(),
        date: date === "" ? null : date,
        // 🚨 급식은 준비물 칸 자체가 없으니 원래 값을 그대로 둔다 (빈 배열로 덮지 않는다).
        items: showItems ? items : entry.items,
        // 🚨 부모가 확인했으므로 더 이상 "확인이 필요한" 항목이 아니다. 이 전환이 저장 대상을 만든다.
        needs_review: false,
        review_reason: undefined,
      },
      // 🚨 넣은 뒤에는 칸이 없으니 앞의 값을 그대로 돌려준다 — 감춘 칸의 값으로 덮지 않는다.
      showEvent && added === undefined ? draftPlan : plan,
    );
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

      {/* ── 캘린더에 넣을 때 ─────────────────────────────────────────────────
          🚨 **여기서 넣지 않는다** (#824). 값만 고치고, 넣을지는 목록 줄의 체크가, 넣는 것은 맨 아래
             버튼이 한다 — 이 칸에 버튼이 서면 저장이 다시 두 군데가 된다.
          🚨 **급식에는 세우지 않는다.** 식단표는 전부 날짜가 있지만 일정이 아니다.
          🚨 **안내에도 세우지 않는다** (#723 · `canBecomeEvent`). */}
      {showEvent ? (
        <section
          aria-labelledby="photo-entry-calendar"
          className="border-line rounded-card flex flex-col gap-3 border p-4"
        >
          <div>
            <h3 id="photo-entry-calendar" className="text-label text-ink-muted">
              캘린더에 넣을 때
            </h3>
            {added ? null : (
              <p className="text-body-sm text-ink-muted mt-1">
                목록에서 캘린더에 넣기를 고르면 이 값으로 들어가요. 넣는 것은 맨 아래 버튼을 누를
                때예요.
              </p>
            )}
          </div>

          {added ? (
            // 🚨 넣은 뒤에는 값을 고칠 칸을 감춘다 — 고쳐도 캘린더의 일정은 그대로다.
            <p role="status" className="text-body-sm text-ink-muted">
              캘린더에 넣었어요. {eventWhen(added.starts_at, added.ends_at, added.all_day)}. 함께
              보는 보호자에게도 보여요.
            </p>
          ) : (
            <>
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
                  열고(`readStartTime`), 없으면 비어서 연다 — 고르기 전에는 목록 줄의 체크가 잠기고,
                  왜 잠겼는지는 그 줄이 말한다. */}
              {!allDay ? <TimeField label="시작 시간" value={time} onChange={setTime} /> : null}

              {/* 🚨 **함께 넣는 끝을 여기서 보여준다.** 보이지 않는 값이 캘린더에 들어가면
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
            </>
          )}
        </section>
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
