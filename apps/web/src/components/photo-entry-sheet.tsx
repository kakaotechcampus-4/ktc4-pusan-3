"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { X } from "lucide-react";
import { useState } from "react";

import { BottomSheet } from "@/components/ui/bottom-sheet";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { DateField } from "@/components/ui/date-field";
import { ICON_SIZE, ICON_STROKE } from "@/components/ui/icon";
import { Spinner } from "@/components/ui/spinner";
import { TextInput } from "@/components/ui/text-input";
import { TimeField } from "@/components/ui/time-field";
import { qk, submitEventDraft } from "@/lib/api";
import type { PhotoEntry } from "@/lib/api/types";
import { useIdempotencyKey } from "@/lib/api/use-idempotency-key";
import { cn } from "@/lib/cn";
import { withSeoulDateTime } from "@/lib/event-draft";
import { formatDay } from "@/lib/format";

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
  eventAdded,
  onEventAdded,
  onClose,
  onSave,
  onRemove,
}: {
  /** `null` 이면 닫혀 있다. 열 때마다 새로 세우려고 호출부가 `key` 를 준다. */
  entry: PhotoEntry | null;
  childId: string;
  /** 이 항목이 **이미 캘린더에 들어갔는가.** 🚨 두 번 넣는 길을 막는다. */
  eventAdded: boolean;
  /** 넣고 나서 호출부에 알린다 — 목록·요약이 "일정 N건 넣음" 을 말해야 한다. */
  onEventAdded: (entryId: string) => void;
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
      title="읽은 내용 고치기"
      description="여기서 고친 값이 저장돼요. 고치기 전에는 아무것도 저장되지 않아요."
    >
      {/* 🚨 열 때마다 처음 값에서 시작한다 — 앞 항목을 고치던 값이 남으면 다른 항목에 덮인다. */}
      {entry ? (
        <EntryForm
          key={entry.id}
          entry={entry}
          childId={childId}
          eventAdded={eventAdded}
          onEventAdded={onEventAdded}
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
  childId,
  eventAdded,
  onEventAdded,
  onSave,
  onRemove,
}: {
  entry: PhotoEntry;
  childId: string;
  eventAdded: boolean;
  onEventAdded: (entryId: string) => void;
  onSave: (next: PhotoEntry) => void;
  onRemove: (id: string) => void;
}) {
  const queryClient = useQueryClient();
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
  const [allDay, setAllDay] = useState(entry.all_day ?? true);
  /**
   * 🚨 **시각은 비어서 시작한다.** 알림장에서 읽어낸 것에 시각이 없기 때문이고, 없는 것을
   *    자정으로 채우면 아무도 고르지 않은 "오전 12:00" 이 캘린더에 들어간다 — 하루 종일을
   *    끄면 실제로 그렇게 나가고 있었다 (#151 · `lib/event-draft.ts` 머리말).
   */
  const [time, setTime] = useState("");

  /** 🚨 한 항목이 사용자 동작 하나다. 재시도는 같은 키로 간다. */
  const eventKey = useIdempotencyKey();

  /** 급식은 준비물을 갖지 않는다 (위 🚨). */
  const showItems = entry.kind !== "meal";

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
    mutationFn: () =>
      submitEventDraft(
        childId,
        {
          event: {
            title: title.trim(),
            starts_at: withSeoulDateTime(date, time, allDay),
            ends_at: null,
            all_day: allDay,
            event_type: "episodic",
            // 🚨 알림장에서 읽은 것은 기관이 알려준 일정이다 (계약서 §09 와 같은 출처 판정).
            category: "institution",
          },
          items: showItems ? items.map((item) => ({ item_id: null, item_name: item })) : [],
        },
        eventKey.current(),
      ),
    onSuccess: async () => {
      // 🚨 성공한 뒤에만 다음 키로 넘어간다. 실패 뒤 다시 누르는 것은 재시도라 같은 키여야 한다.
      eventKey.rotate();
      onEventAdded(entry.id);
      await queryClient.invalidateQueries({ queryKey: qk.child(childId) });
    },
  });

  /**
   * 🚨 제목과 일시가 없으면 넣을 수 없다. 화면이 **왜** 잠겼는지 말한다.
   *
   * 🚨 **막는 것이 여럿이면 전부 말한다** (초안 카드와 같은 규칙). 한동안 첫 번째 것만 말했는데,
   *    시각 칸이 서면서 알림장에서 온 항목은 **날짜도 시각도** 비어 있게 됐다 — 하나씩 말하면
   *    보호자가 날짜를 채운 뒤에야 두 번째 관문을 알게 된다.
   */
  const eventLocks = [
    title.trim().length === 0 ? "무엇인지 적어주셔야 넣을 수 있어요." : null,
    date === "" ? "날짜를 골라주셔야 넣을 수 있어요." : null,
    // 🚨 하루 종일이면 시각을 안 묻는다 — 물을 것이 없는 것이지 안 고른 것이 아니다.
    !allDay && time === "" ? "몇 시에 시작하는지 골라주셔야 넣을 수 있어요." : null,
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
      <DateField
        label="언제"
        hint="못 읽었으면 비워 둬도 돼요. 일정으로 넣으려면 날짜가 있어야 해요."
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
             칸을 세우면 한 달치 급식마다 "일정으로 넣을까" 를 묻게 된다. */}
      {showItems ? (
        <div className="border-line rounded-card border p-4">
          <Checkbox
            checked={eventAdded || asEvent}
            // 🚨 이미 넣은 것은 끌 수 없다 — 끈다고 캘린더에서 빠지지 않는다.
            onChange={(next) => !eventAdded && setAsEvent(next)}
            label="일정으로도 넣기"
            description="켜면 이 내용이 캘린더에 들어가요. 기록으로 남기는 것과는 별개예요."
          />

          {eventAdded ? (
            <p role="status" className="text-body-sm text-ink-muted mt-2">
              캘린더에 넣었어요.{date ? ` ${formatDay(date)}.` : ""} 함께 보는 보호자에게도 보여요.
            </p>
          ) : asEvent ? (
            <div className="mt-3 flex flex-col gap-3">
              <Checkbox checked={allDay} onChange={setAllDay} label="하루 종일" />

              {/* 🚨 **하루 종일이면 칸을 감춘다** (초안 카드와 같은 처리). 알림장에서 읽어낸
                  것에는 시각이 없어서 이 칸은 늘 비어서 시작한다 — 고르기 전에는 아래 버튼이
                  잠기고, 왜 잠겼는지는 버튼 위에서 말한다. */}
              {!allDay ? <TimeField label="시작 시간" value={time} onChange={setTime} /> : null}

              {addEvent.isError ? (
                // 🚨 실패를 빨강으로 칠하지 않는다 (디자인 시스템 §3).
                <p
                  role="status"
                  className="bg-surface-muted rounded-field text-body-sm text-ink-muted px-3 py-2"
                >
                  {addEvent.error instanceof Error ? addEvent.error.message : "넣지 못했어요."} 아직
                  아무것도 넣지 않았어요.
                </p>
              ) : null}

              {/* 🚨 **게이트의 마지막 확인은 버튼 옆에 선다** (`safety-scan-review.tsx` 선례).
                  배너를 하나 더 세우지 않는다 — 색은 `caution` 을 쓰되 글자 한 덩이다. */}
              <div
                className={cn(
                  "text-body-sm flex flex-col gap-1",
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
                onClick={() => addEvent.mutate()}
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
