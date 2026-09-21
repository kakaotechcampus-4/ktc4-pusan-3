"use client";

import { X } from "lucide-react";
import { useState } from "react";

import { BottomSheet } from "@/components/ui/bottom-sheet";
import { Button } from "@/components/ui/button";
import { DateField } from "@/components/ui/date-field";
import { ICON_SIZE, ICON_STROKE } from "@/components/ui/icon";
import { TextInput } from "@/components/ui/text-input";
import type { PhotoEntry } from "@/lib/api/types";

/**
 * 08 사진 — 읽어낸 항목 **하나**를 고치는 시트.
 *
 * 🚨 **여기서 고친 값이 저장되는 값이다.** 모델이 읽은 것을 부모가 덮어쓰는 유일한 자리고,
 *    확인을 누른 항목만 `commit` 에 실린다 (`photo-review.tsx`).
 *
 * 🚨 **승인 게이트가 아니다.** 08 의 저장은 `event` 를 `draft` 로만 만든다 (CLAUDE.md §2) —
 *    `btn-approve` 도 `caution` 도 쓰지 않는다. 확인 버튼은 그냥 primary 다.
 *
 * 🚨 **화면 하나로 만들지 않는다.** 알림장 한 장에 항목이 여러 개라 고칠 때마다 화면을 옮기면
 *    목록에서의 자리를 잃는다 — 시트는 덮고 닫히면 있던 자리로 돌아온다.
 *
 * ⚠️ **시트 안에 시트가 하나 더 열린다** (`DateField` 의 달력). 네이티브 `<dialog>` 는 top layer
 *    를 쌓으므로 달력이 위에 뜨고, 닫히면 이 시트로 포커스가 돌아온다.
 */
export function PhotoEntrySheet({
  entry,
  onClose,
  onSave,
  onRemove,
}: {
  /** `null` 이면 닫혀 있다. 열 때마다 새로 세우려고 호출부가 `key` 를 준다. */
  entry: PhotoEntry | null;
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
        <EntryForm key={entry.id} entry={entry} onSave={onSave} onRemove={onRemove} />
      ) : null}
    </BottomSheet>
  );
}

/** 고를 수 있는 날의 범위. 🚨 알림장은 **다음 주 일정**을 싣기 때문에 미래를 막지 않는다. */
const FROM_DATE = new Date(2020, 0, 1);
const TO_DATE = new Date(new Date().getFullYear() + 2, 11, 31);

function EntryForm({
  entry,
  onSave,
  onRemove,
}: {
  entry: PhotoEntry;
  onSave: (next: PhotoEntry) => void;
  onRemove: (id: string) => void;
}) {
  const [title, setTitle] = useState(entry.title);
  const [date, setDate] = useState(entry.date ?? "");
  const [items, setItems] = useState(entry.items);
  const [adding, setAdding] = useState("");
  const [error, setError] = useState<string | null>(null);

  /** 급식은 준비물을 갖지 않는다 (위 🚨). */
  const showItems = entry.kind !== "meal";

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

      {/* 🚨 날짜는 비워 둘 수 있다. 읽어내지 못한 것을 오늘로 채우지 않는다 —
          날짜가 없으면 일정으로 올리지 않고 내용만 남긴다 (아래 안내). */}
      <DateField
        label="언제"
        hint="못 읽었으면 비워 둬도 돼요. 날짜가 없으면 일정으로 올리지 않아요."
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
