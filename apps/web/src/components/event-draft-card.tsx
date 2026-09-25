"use client";

import { ArrowRight, Plus, X } from "lucide-react";
import { useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardFailed } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { DateField } from "@/components/ui/date-field";
import { ICON_SIZE, ICON_STROKE } from "@/components/ui/icon";
import { Spinner } from "@/components/ui/spinner";
import { TextInput } from "@/components/ui/text-input";
import { cn } from "@/lib/cn";
import { formatEventTime, formatTimeOfDay } from "@/lib/format";
import type { EventDraft, EventDraftFields, EventDraftItem } from "@/lib/api/types";

/**
 * 일정 초안 한 장 — **초안이 만들어지는 세 경로가 같이 쓰는 한 벌.**
 *
 *   ㉠ 한 줄 입력   "금요일에 물놀이 있어"   → 04 저장 결과 레이어 안
 *   ㉡ 제안 카드    activity 제안 "나들이"   → 05 위 바텀시트 (항상 한 장)
 *   ㉢ 사진(알림장) 한 장에 일정 여러 건     → 08 사진 화면
 *
 * 🚨 **그릇은 셋이지만 카드는 한 벌이다** (#121 · #122 합의 "모양이 갈리면 화면이 여러 벌이 된다").
 *    그 합의의 목적은 payload 가 같아서 **그리는 코드가 한 벌**인 것이지 그릇까지 하나여야
 *    한다는 뜻이 아니다 — 한 장을 위해 화면을 따로 띄우거나 N 장을 시트에 밀어넣는 쪽은
 *    이미 두 번 실패한 길이다 (`apps/web/CLAUDE.md` §3 · `safety-scan-review.tsx` 머리말).
 *
 * 🚨 **여기가 승인 게이트 ㉠ 이다.** 제출(`POST /children/{cid}/events`)이 캘린더에 쓴다 —
 *    그래서 `btn-approve` 를 쓴다. 초안을 만드는 호출은 아무것도 쓰지 않으므로 게이트가 아니고,
 *    게이트를 늘린 것이 아니라 **있던 게이트가 이 버튼으로 옮겨온 것**이다 (최상위 §2 — 딱 2곳).
 *
 * 🚨 **제출은 건별이다** (9/21 회의). 카드마다 자기 버튼을 갖고, 성공도 실패도 카드마다 따로다.
 *    ⚠️ 그래서 화면에 카드가 여러 장이면 `btn-approve` 도 여러 개가 된다 — 디자인 시스템 §7 의
 *    "한 화면에 primary 하나" 와 부딪히는 자리다. 목록 항목 예외(§7 — 05 의 "이걸로")로 읽고
 *    있지만, 건별 제출 결정의 결과라 화면이 아니라 **계약에서 온 제약**이라는 점을 적어 둔다.
 *
 * 🚨 **못 읽은 값을 프론트가 채우지 않는다.** 제안에서 온 초안은 일자가 비어 있고
 *    (`starts_at: null`), 보호자가 고르기 전에는 제출 버튼이 잠긴다. 오늘로 기본값을 넣으면
 *    "확인하고 넣은 것" 과 "들어간 것" 이 달라진다 (`PhotoEntry.date` 와 같은 규칙).
 *
 * 🚨 **`items` 는 최종 목록이다** (#122 확정). 배열에서 빠진 `item_id` 가 삭제를 표현하는
 *    유일한 방법이라, 지우기가 화면에 있어야 계약이 성립한다.
 * 🚨 **준비물에 체크박스를 그리지 않는다.** `is_prepared` 는 payload 에 없고 체크는
 *    `PATCH /event-items/{iid}` 만의 몫이다 — 여기서 건드리면 제출하는 순간 풀린다.
 *
 * 🚨 **알레르기 사전검사(`prechecks`)를 카드가 들고 있지 않다.** 그건 승인 게이트 ㉡ 이고
 *    제안 경로에만 온다 — 세 경로가 공유하는 카드에 두면 나머지 둘이 빈 칸을 진다.
 *    묻는 자리는 그릇(승인 시트)이고, 카드는 그 결과만 `blocked` · `lockReason` 으로 받는다.
 *
 * 🚨 **도메인 색을 쓰지 않는다.** `category` 는 Agent 도메인이 아니다 (이름이 겹치는
 *    `activity` 가 있지만 뜻이 다르다) — `photo-review.tsx` 와 같은 판단이다.
 */

/** 게이트를 통과한 뒤. 🚨 보호자의 선택이 아니라 **서버가 저장을 끝냈는가**다. */
export type DraftSubmitState = "idle" | "submitting" | "submitted" | "failed";

/**
 * 고를 수 있는 날의 범위. 🚨 **미래를 막지 않는다** — 일정은 앞으로의 일이고, 알림장은
 * 다음 주 것을 싣는다 (`photo-entry-sheet.tsx` 와 같은 판단·같은 값).
 */
const FROM_DATE = new Date(2020, 0, 1);
const TO_DATE = new Date(new Date().getFullYear() + 2, 11, 31);

export function EventDraftCard({
  draft,
  state = "idle",
  error,
  blocked = false,
  lockReason,
  onSubmit,
}: {
  draft: EventDraft;
  state?: DraftSubmitState;
  /** 제출이 실패한 이유. `state === "failed"` 일 때만 쓴다. */
  error?: string;
  /**
   * 🚨 알레르기가 확인돼 이 초안은 넣지 않는다. 그릇이 판정한다 —
   *    사전검사에 답하는 자리는 카드 밖(승인 시트)이고, 여기는 결과만 받는다.
   */
  blocked?: boolean;
  /**
   * 그릇이 거는 잠금과 그 이유. 🚨 **`blocked` 와 다르다** — `blocked` 는 "안 넣는다"(알레르기)고
   *    이건 "아직 못 넣는다"(제안 경로의 재료 확인이 안 끝남)다. 둘을 한 값으로 쓰면 화면이
   *    아직 답하지 않은 보호자에게 "이 일정은 넣지 않을게요" 라고 말한다.
   */
  lockReason?: string;
  /** 🚨 보호자가 확인한 **최종 상태**를 통째로 넘긴다 (#122 확정 — 부분 갱신이 아니다). */
  onSubmit: (final: { event: EventDraftFields; items: EventDraftItem[] }) => void;
}) {
  /**
   * 🚨 **정본은 여기다.** 보호자가 고치면 이 값이 바뀌고 제출은 이 값을 보낸다 —
   *    서버가 보낸 `draft` 를 다시 읽지 않는다 (`photo-review.tsx` 와 같은 구조).
   */
  const [event, setEvent] = useState<EventDraftFields>(() => draft.event);
  const [items, setItems] = useState<EventDraftItem[]>(() => draft.items);
  const [newItem, setNewItem] = useState("");
  /**
   * 🚨 **넣은 뒤에 보여줄 것은 로컬 값이 아니라 보낸 값이다.** 제출이 나가는 동안에도 칸은
   *    살아 있어서, 기다리는 사이 날짜를 바꾸면 "넣었어요" 카드가 **캘린더에 안 들어간 값**을
   *    보여준다 — 승인 게이트가 자기가 쓴 것과 다른 것을 말하게 된다.
   */
  const [sent, setSent] = useState<{ event: EventDraftFields; items: EventDraftItem[] } | null>(
    null,
  );
  /**
   * 🚨 **"하루 종일" 을 껐다 켜도 원래 시각이 살아 있어야 한다.** 켤 때 `starts_at` 을 자정으로
   *    덮어썼더니, 끄는 순간 그 자정을 도로 읽어서 10시에 온 초안이 0시가 됐다.
   */
  const originalStartsAt = useRef(draft.event.starts_at);

  const date = seoulDate(event.starts_at);
  const missingDate = date === "";
  const done = state === "submitted";
  const busy = state === "submitting";

  /** 🚨 일자가 없으면 제출할 수 없다. 화면이 왜 잠겼는지 말한다 — 조용히 비활성으로 두지 않는다. */
  const canSubmit =
    !missingDate && event.title.trim() !== "" && !blocked && !lockReason && !busy && !done;

  function patch(next: Partial<EventDraftFields>) {
    setEvent((prev) => ({ ...prev, ...next }));
  }

  /**
   * 넣고 난 카드. 🚨 **그릇이 하는 말을 되풀이하지 않는다** — 승인 시트는 제목이 이미
   *    "캘린더에 넣었어요" 라서, 카드가 같은 문장을 다시 쓰면 한 화면에 두 번 선다.
   *    카드가 지는 것은 **어느 초안이 들어갔는가**뿐이고, 그래서 머리줄의
   *    "아직 저장되지 않았어요" 자리를 같은 모양의 칩이 대신한다 (04·08 의 N장에서는
   *    이 칩이 카드마다 결과를 말하는 유일한 표시다).
   */
  if (done) {
    const shown = sent ?? { event, items };
    return (
      <Card>
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
          <span className="text-label text-ink-subtle">
            {draft.op === "update" ? "일정 수정" : "새 일정"}
          </span>
          <span className="text-caption text-brand-ink bg-brand-soft rounded-field px-2 py-0.5">
            넣었어요
          </span>
        </div>
        <p className="text-body text-ink mt-3">{shown.event.title}</p>
        {shown.event.starts_at ? (
          <p className="text-body-sm text-ink-muted mt-1">
            {formatEventTime(shown.event.starts_at, shown.event.all_day)}
          </p>
        ) : null}
      </Card>
    );
  }

  return (
    <Card>
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <span className="text-label text-ink-subtle">
          {draft.op === "update" ? "일정 수정" : "새 일정"}
        </span>
        {/* 🚨 아직 아무것도 저장되지 않았다는 것은 경고가 아니라 **사실**이라 중립 면이다
            (`photo-review.tsx` 와 같은 판단 — `caution` 은 누르는 버튼 옆에만 선다). */}
        <span className="text-caption text-ink-muted bg-surface-muted rounded-field px-2 py-0.5">
          아직 저장되지 않았어요
        </span>
      </div>

      {/* 🚨 왜 확인이 필요한지는 **서버 문구**를 그대로 쓴다. 프론트가 지어내지 않는다. */}
      {draft.needs_review ? (
        <p className="text-body-sm text-ink-muted bg-surface-muted rounded-field mt-3 px-3 py-2">
          {draft.review_reason ?? "읽지 못한 값이 있어요. 보고 채워 주세요."}
        </p>
      ) : null}

      <div className="mt-4 flex flex-col gap-4">
        <TextInput
          label="제목"
          value={event.title}
          disabled={busy}
          onChange={(e) => patch({ title: e.target.value })}
        />

        {/* 🚨 일자는 **비어서 올 수 있다** — 제안 문장만으로는 언제인지 알 수 없고, 알림장에서
               못 읽어내기도 한다.
            🚨 `placeholder` 를 여기서 정한다. 화면마다 고르는 날의 뜻이 달라서
               `DateField` 에 문구를 박아 두면 다른 화면에서 거짓말이 된다.
            🚨 **hint 에 출처를 적지 않는다.** 한동안 "이 제안만으로는" 이라고 써 뒀는데, 같은 카드를
               사진 경로가 쓰는 순간 제안이 아닌 것을 제안이라고 말한다. 왜 비었는지는 경로를 아는
               쪽(`review_reason`)이 말하고, 여기는 **비었다는 사실만** 말한다. 그것마저 위에 있으면
               같은 말을 두 번 하게 되므로 그때는 아예 뺀다. */}
        <DateField
          label="언제"
          placeholder="날짜를 골라주세요"
          hint={missingDate && !draft.review_reason ? "이 초안에는 날짜가 없어요." : undefined}
          value={date}
          onChange={(next) => patch(moveToDate(event, next, originalStartsAt.current))}
          fromDate={FROM_DATE}
          toDate={TO_DATE}
        />

        <Checkbox
          checked={event.all_day}
          onChange={(checked) =>
            patch({
              all_day: checked,
              // 🚨 켤 때 자정으로 덮되, 끌 때는 **처음 온 시각**을 되살린다.
              starts_at: withSeoulDate(
                checked ? event.starts_at : originalStartsAt.current,
                date,
                checked,
              ),
            })
          }
          label="하루 종일"
        />

        {/* 시각을 고르는 칸은 아직 없다 — 읽어온 시각이 있으면 그대로 보여주기만 한다.
            ⚠️ 보호자가 시각을 직접 정하는 경로는 09 캘린더 것이고, 초안 시트에 필요한지는 미정이다.
            🚨 **날짜를 다시 쓰지 않는다.** 바로 위 `DateField` 가 이미 날짜를 지고 있어서
               `formatEventTime` 을 쓰면 같은 날이 두 표기로 두 번 선다 — 시각만 낸다. */}
        {!event.all_day && event.starts_at ? (
          <p className="text-body-sm text-ink-muted">
            <span className="text-label text-ink-subtle mr-2">몇 시</span>
            {formatTimeOfDay(event.starts_at)}
          </p>
        ) : null}

        <ItemList items={items} disabled={busy} onChange={setItems} />

        {/* 🚨 `TextInput` 의 `className` 은 래퍼가 아니라 `<input>` 으로 간다 — 폭은 바깥에서 준다. */}
        <div className="flex items-end gap-2">
          <div className="min-w-0 flex-1">
            <TextInput
              label="준비물 더하기"
              value={newItem}
              disabled={busy}
              onChange={(e) => setNewItem(e.target.value)}
              onKeyDown={(e) => {
                if (e.key !== "Enter") return;
                e.preventDefault();
                addItem();
              }}
            />
          </div>
          <Button
            variant="secondary"
            size="compact"
            disabled={busy || newItem.trim() === ""}
            onClick={addItem}
          >
            <Plus aria-hidden size={ICON_SIZE.sm} strokeWidth={ICON_STROKE} />
            더하기
          </Button>
        </div>
      </div>

      {/* 🚨 수정 초안은 **무엇이 달라지는지**가 본체다. `before` 와 지금 값을 화면이 직접 비교한다 —
          서버가 보내 준 `changed` 는 보호자가 값을 고치는 순간 쓸 수 없어서 payload 에서 뺐다 (#122). */}
      {draft.before ? <Diff before={draft.before} event={event} items={items} /> : null}

      {error ? (
        <CardFailed className="mt-4">
          <p>{error}</p>
          <p className="mt-1">아직 아무것도 넣지 않았어요.</p>
        </CardFailed>
      ) : null}

      <div className="mt-5">
        {blocked ? (
          <p className="text-body-sm text-danger-ink">
            알레르기를 확인했어요. 이 일정은 넣지 않을게요.
          </p>
        ) : (
          <>
            {/* 🚨 **게이트의 마지막 확인은 버튼 옆에 선다** (`safety-scan-review.tsx` 선례).
                배너를 하나 더 세우지 않는다 — 배너가 쌓이면 그때부터 아무도 안 읽는다.
                색은 `caution` 을 쓰되 배너가 아닌 **글자 한 덩이**다. 이 버튼이 게이트라 그 색을
                쓸 수 있고(§3 — 게이트 2곳 전용), 카드 면 위라 `caution-ink` 가 아니라 `caution` 이다. */}
            {/* 🚨 **막는 것이 둘이면 둘 다 말한다.** 날짜만 먼저 말하면 보호자가 그것을 채운 뒤에야
                두 번째 관문을 알게 된다 — 제안 경로는 일자가 늘 비어 있어서 늘 그랬다. */}
            <div
              className={cn(
                "mb-3 flex flex-col gap-1",
                canSubmit ? "text-caution" : "text-ink-subtle",
              )}
            >
              {lockReason ? <p className="text-body-sm">{lockReason}</p> : null}
              {missingDate ? (
                <p className="text-body-sm">날짜를 골라주셔야 넣을 수 있어요.</p>
              ) : null}
              {canSubmit ? (
                <p className="text-body-sm">
                  누르면 캘린더에 들어가요. 함께 보는 보호자에게도 보여요.
                </p>
              ) : null}
            </div>
            <Button
              variant="approve"
              disabled={!canSubmit}
              aria-busy={busy}
              onClick={() => {
                setSent({ event, items });
                onSubmit({ event, items });
              }}
            >
              {busy ? <Spinner /> : null}
              {busy ? "넣는 중이에요" : "확인했어요, 캘린더에 넣을게요"}
            </Button>
          </>
        )}
      </div>
    </Card>
  );

  function addItem() {
    const name = newItem.trim();
    if (name === "") return;
    // 🚨 새 준비물은 `item_id: null` 이다 — 제출에서 INSERT 로 갈린다.
    setItems((prev) => [...prev, { item_id: null, item_name: name }]);
    setNewItem("");
  }
}

/**
 * 준비물 목록. 🚨 **지우기가 있어야 계약이 성립한다** — `items` 가 최종 목록이라
 * 배열에서 빠지는 것이 삭제를 표현하는 유일한 방법이다 (#122).
 */
function ItemList({
  items,
  disabled,
  onChange,
}: {
  items: EventDraftItem[];
  disabled: boolean;
  onChange: (next: EventDraftItem[]) => void;
}) {
  if (items.length === 0) {
    return <p className="text-body-sm text-ink-subtle">준비물은 없어요.</p>;
  }

  return (
    <div>
      <p className="text-label text-ink-muted">준비물</p>
      <ul className="mt-1.5 flex flex-col gap-1.5">
        {items.map((item, index) => (
          <li
            key={item.item_id ?? `new-${index}-${item.item_name}`}
            className="border-line rounded-field flex items-center gap-2 border px-3 py-2"
          >
            <span className="text-body-sm text-ink flex-1">{item.item_name}</span>
            {item.item_id === null ? (
              <span className="text-caption text-ink-subtle">새로 더한 것</span>
            ) : null}
            <button
              type="button"
              disabled={disabled}
              aria-label={`${item.item_name} 빼기`}
              onClick={() => onChange(items.filter((_, i) => i !== index))}
              className="text-ink-subtle hover:text-ink min-h-touch -my-2 shrink-0 px-1"
            >
              <X aria-hidden size={ICON_SIZE.sm} strokeWidth={ICON_STROKE} />
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

/**
 * 수정 초안에서 **무엇이 달라지는지**. 🚨 바뀐 필드 이름만으로는 부족하다 —
 * 보호자가 보고 싶은 것은 "오후 3시 → 오후 5시" 라는 값의 변화다 (#122 리뷰).
 */
function Diff({
  before,
  event,
  items,
}: {
  before: EventDraft["before"] & object;
  event: EventDraftFields;
  items: EventDraftItem[];
}) {
  const rows: Array<{ term: string; from: string; to: string }> = [];

  if (before.title !== event.title) {
    rows.push({ term: "제목", from: before.title, to: event.title });
  }
  // 🚨 문자열이 아니라 **순간**으로 비교한다. `+09:00` 과 `Z` 처럼 표기만 다른 같은 시각을
  //    "달라졌다" 고 읽으면 "오후 3:00 → 오후 3:00" 같은 줄이 선다.
  if (!sameInstant(before.starts_at, event.starts_at) || before.all_day !== event.all_day) {
    rows.push({
      term: "일시",
      from: before.starts_at ? formatEventTime(before.starts_at, before.all_day) : "없음",
      to: event.starts_at ? formatEventTime(event.starts_at, event.all_day) : "없음",
    });
  }

  const keptIds = new Set(items.map((item) => item.item_id).filter((v): v is string => v !== null));
  const removed = before.items.filter((item) => item.item_id && !keptIds.has(item.item_id));
  const added = items.filter((item) => item.item_id === null);

  if (rows.length === 0 && removed.length === 0 && added.length === 0) {
    return (
      <p className="text-body-sm text-ink-muted border-line mt-4 border-t pt-3">
        원래 일정과 달라진 것이 없어요.
      </p>
    );
  }

  return (
    <div className="border-line mt-4 border-t pt-3">
      <p className="text-label text-ink-muted">이렇게 달라져요</p>
      <dl className="mt-2 flex flex-col gap-1.5">
        {rows.map((row) => (
          <div key={row.term} className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
            <dt className="text-caption text-ink-subtle w-10 shrink-0">{row.term}</dt>
            <dd className="text-body-sm text-ink-muted flex flex-wrap items-baseline gap-1.5">
              <span className="line-through">{row.from}</span>
              <ArrowRight
                aria-hidden
                size={ICON_SIZE.sm}
                strokeWidth={ICON_STROKE}
                className="translate-y-0.5"
              />
              <span className="text-ink">{row.to}</span>
            </dd>
          </div>
        ))}
        {added.length > 0 ? (
          <div className="flex flex-wrap items-baseline gap-x-2">
            <dt className="text-caption text-ink-subtle w-10 shrink-0">더함</dt>
            <dd className="text-body-sm text-ink">
              {added.map((item) => item.item_name).join(", ")}
            </dd>
          </div>
        ) : null}
        {removed.length > 0 ? (
          <div className="flex flex-wrap items-baseline gap-x-2">
            <dt className="text-caption text-ink-subtle w-10 shrink-0">뺌</dt>
            <dd className="text-body-sm text-ink-muted line-through">
              {removed.map((item) => item.item_name).join(", ")}
            </dd>
          </div>
        ) : null}
      </dl>
    </div>
  );
}

/* ── 날짜 조립 ────────────────────────────────────────────────────────── */

/**
 * 🚨 **날짜를 계산하는 것이 아니라 조립하는 것이다.** 보호자가 달력에서 고른 날을 ISO 로
 *    옮겨 담을 뿐이고, 나이·기간·상대 시간은 여전히 만들지 않는다 (CLAUDE.md §3 · `format.ts` 머리말).
 *    제출받는 쪽이 `check_when` 으로 다시 검증한다 (#122 확정) — 규칙 쪽 방어는 서버에 있다.
 *
 * ⚠️ 시간대를 기기 설정이 아니라 `Asia/Seoul` 로 고정한다. 승인 게이트 앞에서 기기 시간대가
 *    다르면 확인한 날과 들어간 날이 달라진다 (`format.ts` 와 같은 이유).
 */
const SEOUL_OFFSET = "+09:00";

const SEOUL_YMD = new Intl.DateTimeFormat("en-CA", {
  timeZone: "Asia/Seoul",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
});

const SEOUL_HMS = new Intl.DateTimeFormat("en-GB", {
  timeZone: "Asia/Seoul",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hourCycle: "h23",
});

/** 두 ISO 시각이 같은 순간인가. 표기가 달라도 같으면 참이다. */
function sameInstant(a: string | null, b: string | null): boolean {
  if (a === b) return true;
  if (!a || !b) return false;
  const left = Date.parse(a);
  const right = Date.parse(b);
  return !Number.isNaN(left) && !Number.isNaN(right) && left === right;
}

/**
 * 고른 날짜로 옮긴다. 🚨 **`ends_at` 을 두고 가지 않는다** — `starts_at` 만 옮기면 끝이 시작보다
 *    앞선 일정이 제출된다. 걸린 시간(`ends_at - starts_at`)을 유지한 채로 같이 옮긴다.
 */
function moveToDate(
  event: EventDraftFields,
  date: string,
  originalStartsAt: string | null,
): Partial<EventDraftFields> {
  const starts = withSeoulDate(
    event.all_day ? event.starts_at : originalStartsAt,
    date,
    event.all_day,
  );
  if (!event.ends_at || !event.starts_at || !starts) return { starts_at: starts };

  const span = Date.parse(event.ends_at) - Date.parse(event.starts_at);
  if (Number.isNaN(span)) return { starts_at: starts };
  return { starts_at: starts, ends_at: new Date(Date.parse(starts) + span).toISOString() };
}

/** ISO 시각 → 서울 기준 `YYYY-MM-DD`. 🚨 값이 없으면 **빈 문자열**이다 — 오늘로 채우지 않는다. */
function seoulDate(iso: string | null): string {
  if (!iso) return "";
  const at = new Date(iso);
  return Number.isNaN(at.getTime()) ? "" : SEOUL_YMD.format(at);
}

/** 고른 날짜를 원래 시각에 얹는다. 시각을 모르거나 하루 종일이면 자정이다. */
function withSeoulDate(iso: string | null, date: string, allDay: boolean): string | null {
  if (date === "") return null;
  const at = iso ? new Date(iso) : null;
  const time = !allDay && at && !Number.isNaN(at.getTime()) ? SEOUL_HMS.format(at) : "00:00:00";
  return `${date}T${time}${SEOUL_OFFSET}`;
}
