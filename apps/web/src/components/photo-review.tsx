"use client";

import { ChevronDown, ChevronUp } from "lucide-react";
import { useState } from "react";

import { PhotoEntrySheet, type AddedEventInput } from "@/components/photo-entry-sheet";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Chip, ChipRow } from "@/components/ui/chip";
import { ICON_SIZE, ICON_STROKE } from "@/components/ui/icon";
import { PhotoCard } from "@/components/ui/photo-card";
import { Spinner } from "@/components/ui/spinner";
import { cn } from "@/lib/cn";
import { formatDay, formatEventTime, formatTimeOfDay } from "@/lib/format";
import type { ParsedEvent, PhotoCommitRequest, PhotoEntry, PhotoLane } from "@/lib/api";

/**
 * 08 사진으로 적기 — 읽어낸 것을 보호자가 확인하는 칸.
 *
 * 🚨 **여기는 승인 게이트가 아니다.** 문서 lane 이 만드는 `event` 는 `draft` 고, 캘린더에 쓰는
 *    되돌릴 수 없는 지점은 여전히 `POST /events/{eid}/confirm` 하나다 (CLAUDE.md §2 —
 *    승인 게이트를 늘리지도 줄이지도 않는다). 그래서 `btn-approve` 도 `caution` 도 쓰지 않는다.
 *    "아직 저장하지 않았어요" 는 경고가 아니라 **사실**이라 중립 면(`surface-muted`)에 앉힌다.
 *
 * 🚨 **한 장에서 항목이 여러 개 나온다.** 알림장 한 장에 일정이 여러 개 적혀 있고 식단표는
 *    거의 한 달치다. 그래서 화면이 두 무리로 가른다 —
 *    ㉠ **확인이 필요한 것**(값을 못 읽음)을 위에 펼쳐 두고,
 *    ㉡ **잘 읽은 것**은 아래에 **접어** 둔다. 스무 개가 한 줄씩 늘어서면 정작 봐야 할
 *    두세 개가 묻힌다 — 부모가 볼 것은 "기계가 못 읽은 곳" 이다.
 *
 * 🚨 **확인하지 않은 항목은 저장되지 않는다.** 고치기 시트에서 확인을 눌러야 저장 대상이 된다 —
 *    못 읽은 값을 그대로 넣으면 "승인 전에는 저장되지 않아요" 가 문구만 남는다.
 *    화면이 몇 건이 빠지는지 숫자로 말한다.
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
    footnote: "태그는 성향이나 발달로 확정하지 않아요. 알레르기나 건강 정보로도 쓰지 않아요.",
  },
};

const ENTRY_KIND_LABEL: Record<PhotoEntry["kind"], string> = {
  event: "일정",
  supply: "준비물",
  meal: "급식",
};

/**
 * 줄을 여는 버튼의 문구.
 *
 * 🚨 **이 버튼이 여는 곳에서 캘린더에 쓴다.** "고치기" 만 적어 두니 그 일이 이 안에 있다는 것이
 *    목록 어디에도 안 보였다 — 부모는 열어 보고 나서야 알고, 안 열어 본 줄은 못 넣는다.
 * 🚨 **급식에는 일정 칸이 아예 없다** (`photo-entry-sheet.tsx` — 식단표는 전부 날짜가 있지만
 *    일정이 아니다). 없는 기능을 문구로 약속하지 않는다.
 * 🚨 **이미 넣은 줄도 약속하지 않는다.** 두 번 넣는 길은 시트가 막아 뒀고, 그 줄에서 남은 일은
 *    고치는 것뿐이다.
 */
function editLabel(entry: PhotoEntry, added: boolean): string {
  if (entry.kind === "meal" || added) return "고치기";
  return "고치기 · 일정 넣기";
}

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
  commitError: string | null;
  /** 저장하지 않고 03 홈으로. */
  onGoHome: () => void;
  /** 4:3 칸에 그릴 사진. 업로드한 파일의 objectURL 이다. */
  previewUrl: string;
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
  /**
   * 🚨 **읽어낸 항목의 정본은 여기다.** 부모가 고치면 이 배열이 바뀌고, 저장은 이 값을 보낸다 —
   *    서버가 보낸 `parsed` 를 다시 읽지 않는다.
   */
  const [entries, setEntries] = useState<PhotoEntry[]>(() => parsed.entries ?? []);
  const [tags, setTags] = useState<string[]>([]);
  const [editing, setEditing] = useState<PhotoEntry | null>(null);
  /**
   * 이미 캘린더에 넣은 항목 → **넣은 일시.** 🚨 **저장(관찰)과 다른 축이다** — 일정은 고치기
   *    시트의 승인 게이트가 그 자리에서 넣고, 아래 "이 내용으로 저장" 은 기록을 여러 건 한 번에
   *    보낸다. 두 개를 한 상태로 묶으면 "일정만 넣고 기록은 안 남긴" 경우를 표현할 수 없다.
   *
   * 🚨 **건수만 들지 않는다.** 목록의 그 줄이 "언제로 넣었는지" 까지 말해야 부모가 확인하러
   *    캘린더에 가지 않는다 — 시트를 닫으면 방금 고른 일시는 화면 어디에도 안 남는다.
   */
  const [eventAdded, setEventAdded] = useState<Record<string, AddedEventInput>>({});
  const addedCount = Object.keys(eventAdded).length;
  const [showChecked, setShowChecked] = useState(false);

  const copy = LANE[lane];

  function saveEntry(next: PhotoEntry) {
    setEntries((prev) => prev.map((e) => (e.id === next.id ? next : e)));
    setEditing(null);
  }

  /**
   * 캘린더에 넣었다 — 목록의 그 줄을 **넣은 것으로** 표시하고, 넣으면서 확인한 값을 얹는다.
   *
   * 🚨 **`saveEntry` 를 쓰지 않는다** — 그쪽은 시트를 닫는다. 여기서 닫으면 부모는 방금 넣은
   *    결과("캘린더에 넣었어요")를 못 보고, 같은 시트에서 이어 하려던 일이 끊긴다.
   * 🚨 **확인한 것으로 넘긴다** (`needs_review: false`). 승인 게이트를 지난 값이라 시트의
   *    "이 내용으로 확인" 보다 무거운 확인이다 — 그대로 두면 화면이 **방금 부모가 확인한 날짜**를
   *    "못 읽었어요 · 고쳐야 저장돼요" 라고 계속 말한다.
   * 🚨 **그래도 기록이 저장되는 것은 아니다.** 저장은 아래 버튼 하나이고, 빼고 싶으면 시트의
   *    "이 항목은 저장하지 않기" 가 그대로 있다 — 두 축은 여전히 따로다.
   */
  function markEventAdded(id: string, added: AddedEventInput) {
    setEventAdded((prev) => ({ ...prev, [id]: added }));
    /**
     * 🚨 **줄이 어디로 갔는지 보여준다.** 확인된 줄은 접힌 "잘 읽었어요" 로 옮겨 가는데,
     *    그대로 두면 방금 캘린더에 넣은 줄이 **화면에서 사라진 것처럼** 보인다 — 넣었다는
     *    표시를 달아 놓고 그 표시를 접어 두는 셈이다.
     */
    setShowChecked(true);
    setEntries((prev) =>
      prev.map((entry) =>
        entry.id === id
          ? {
              ...entry,
              title: added.title,
              date: added.date === "" ? null : added.date,
              items: added.items,
              needs_review: false,
              review_reason: undefined,
            }
          : entry,
      ),
    );
  }

  function removeEntry(id: string) {
    setEntries((prev) => prev.filter((e) => e.id !== id));
    setEditing(null);
  }

  const needsReview = entries.filter((e) => e.needs_review);
  const checked = entries.filter((e) => !e.needs_review);

  /**
   * 🚨 **`attach_to_calendar` 는 이제 활동 lane 만의 것이다** — 그 lane 에서는 "사진을 그날
   *    캘린더에 함께 남길까" 라는 뜻이다 (계약서 §09 의 부수 효과 표).
   *
   * 🚨 **문서 lane 은 언제나 `false` 다.** 일정은 고치기 시트의 승인 게이트가 그 자리에서 넣는다 —
   *    저장이 일정까지 만들면 같은 일을 두 곳이 하게 되고, 부모는 자기가 안 고른 일정이
   *    캘린더에 들어간 것을 나중에 발견한다 (최상위 §2 — 되돌릴 수 없는 것은 사람이 고른다).
   *
   * 🚨 **날짜를 만들지 않는다.** 활동은 화면이 들고 온 날짜를 그대로 쓴다 (apps/web/CLAUDE.md §4).
   */
  const attachDate = lane === "activity" ? date : null;
  const attachToCalendar = lane === "activity" && attachDate !== null;

  /**
   * 🚨 **활동 lane 은 태그를 하나도 안 고르면 저장할 수 없다.** 그 상태로 눌리면 화면이 바로
   *    위에서 한 "고르지 않은 태그는 저장하지 않아요" 를 스스로 뒤집는다.
   *    문서 lane 은 확인된 항목이 하나라도 있어야 한다 — 빈 저장을 만들지 않는다.
   */
  const hasSomethingToSave = lane === "activity" ? tags.length > 0 : checked.length > 0;
  const canSave = !committing && hasSomethingToSave;

  function commit() {
    onCommit(
      lane === "activity"
        ? { lane, selected_tags: tags, attach_to_calendar: attachToCalendar }
        : { lane, entries: checked, attach_to_calendar: attachToCalendar },
    );
  }

  return (
    <div className="flex flex-col gap-6">
      {/* 사진과 거기서 읽어낸 글자가 한 덩어리다 — 이 화면에서 `accent` 는 여기 한 장뿐이다. */}
      <PhotoCard
        tone="accent"
        footer={
          <div className="flex flex-col gap-3">
            {/* 🚨 바꾸는 버튼을 두지 않는다 — 시트에서 고르고 시작했다 (위 주석). */}
            <p className="text-body text-ink">{copy.read}</p>
            <p className="text-body-sm text-ink-muted">{copy.note}</p>

            {lane === "document" && parsed.raw_text ? (
              <details className="group border-line border-t pt-3">
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
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src={previewUrl} alt="방금 넣은 사진" className="h-full w-full object-cover" />
      </PhotoCard>

      {lane === "activity" ? (
        <TagPicker tags={parsed.tags ?? []} selected={tags} onChange={setTags} busy={committing} />
      ) : (
        <>
          <NeedsReviewGroup entries={needsReview} added={eventAdded} onEdit={setEditing} />
          <CheckedGroup
            entries={checked}
            added={eventAdded}
            open={showChecked}
            onToggle={() => setShowChecked((v) => !v)}
            onEdit={setEditing}
          />
        </>
      )}

      <Card>
        <p className="text-section text-ink">이렇게 저장할게요</p>
        {/* 🚨 중립 면이다. 사실을 말하는 자리라 경고색을 쓰지 않는다 (위 주석). */}
        <p className="text-body-sm text-ink-muted bg-surface-muted rounded-field mt-2 px-3 py-2">
          지금까지는 아무것도 저장되지 않았어요. 아래를 눌러야 저장돼요.
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
                  질문("무엇이 저장되나")의 나머지 절반이라 나란히 서야 읽힌다. 면을 하나 더
                  세우면 이 카드에 회색 상자가 세 겹으로 쌓이고, 그러면 아무도 안 읽는다. */}
              {needsReview.length > 0 ? (
                <PreviewRow
                  term="확인 안 한 것"
                  value={`${needsReview.length}건 · 저장하지 않아요`}
                />
              ) : null}
            </>
          )}
          {attachDate ? (
            <PreviewRow
              term={lane === "document" ? "일시" : "남기는 날"}
              value={formatDay(attachDate)}
            />
          ) : null}
          <PreviewRow
            term="출처"
            value={lane === "document" ? "기관이 보낸 문서" : "사진 태그 · 보호자 확인"}
          />
        </dl>

        {commitError ? (
          // 🚨 실패를 빨강으로 칠하지 않는다 (디자인 시스템 §3).
          <p
            role="status"
            className="bg-surface-muted rounded-field text-body-sm text-ink-muted mt-4 p-3"
          >
            {commitError}
          </p>
        ) : null}

        <Button block className="mt-4" disabled={!canSave} aria-busy={committing} onClick={commit}>
          {committing ? <Spinner /> : null}
          {committing ? "저장하는 중이에요" : "이 내용으로 저장"}
        </Button>

        {/* 🚨 **저장 버튼이 일정을 만들지 않는다고 분명히 말한다.** 한동안 "일정 초안으로 함께
            올라가요" 라고 적어 뒀는데, 이제 일정은 고치기 시트에서만 들어간다 —
            문구가 남으면 부모는 저장만 누르고 일정도 됐다고 믿는다.
            🚨 **문서 lane 에서 "기록" 이라고 쓰지 않는다.** 이 제품에서 "기록" 은 **관찰**을
            가리키는 화면 용어인데(apps/web/CLAUDE.md §3), 알림장·급식표는 아이 기록이 아니라
            **기관 문서 행**으로 들어간다(`notice` · `daycare_meal` · AI 파트 data_model).
            실제로 이 화면 위쪽이 "아이의 기록으로는 쌓지 않아요" 라고 말하고 있어서, 아래에서
            "기록이에요" 라고 하면 **한 화면이 서로 반대말**을 했다.
            🚨 **넣은 일정도 여기서 함께 말한다.** 요약이 기록만 세면 부모는 아직 아무 일정도
            안 넣은 줄 안다. 회색 면을 따로 세우지 않는 것은 같은 줄에 들어가는 말이기 때문이다 —
            "저장 버튼이 무엇을 하고 무엇을 안 하는가" 한 문장이다. */}
        <p className="text-caption text-ink-subtle mt-2">
          {lane === "activity"
            ? attachDate
              ? `저장하면 이 사진도 ${formatDay(attachDate)} 캘린더에 함께 남아요.`
              : "남길 날짜가 없어서 캘린더에는 붙이지 않아요."
            : addedCount > 0
              ? `여기서 저장하는 것은 기록이에요. 일정 ${addedCount}건은 이미 캘린더에 넣었어요(저장과는 따로예요).`
              : "여기서 저장하는 것은 읽은 내용이에요. 일정은 항목을 열어 따로 넣어요."}
        </p>
      </Card>

      {/* 🚨 저장하지 않고 나가는 길. 저장된 것이 없으므로 확인을 묻지 않는다 —
          승인 게이트는 2곳뿐이고 여기는 그 2곳이 아니다 (CLAUDE.md §2). */}
      <div>
        <Button variant="secondary" onClick={onGoHome}>
          메인으로 돌아가기
        </Button>
      </div>

      <p className="text-caption text-ink-subtle">{copy.footnote}</p>

      <PhotoEntrySheet
        entry={editing}
        childId={childId}
        eventAdded={editing !== null && editing.id in eventAdded}
        onEventAdded={markEventAdded}
        onClose={() => setEditing(null)}
        onSave={saveEntry}
        onRemove={removeEntry}
      />
    </div>
  );
}

/* ── 확인이 필요한 것 ─────────────────────────────────────────────────── */

/**
 * 🚨 **펼친 채로 맨 위에 둔다.** 부모가 이 화면에서 할 일은 "기계가 못 읽은 곳을 메우는 것"
 *    하나고, 그게 화면 아래에 있으면 스무 줄을 지나야 닿는다.
 */
function NeedsReviewGroup({
  entries,
  added,
  onEdit,
}: {
  entries: PhotoEntry[];
  added: Record<string, AddedEventInput>;
  onEdit: (entry: PhotoEntry) => void;
}) {
  if (entries.length === 0) return null;

  return (
    <section className="flex flex-col gap-2">
      <div>
        <h2 className="text-label text-brand">확인이 필요해요 {entries.length}건</h2>
        <p className="text-body-sm text-ink-muted mt-1">못 읽은 곳이 있어요. 고쳐야 저장돼요.</p>
      </div>
      <ul className="flex flex-col gap-2">
        {entries.map((entry) => (
          <li key={entry.id}>
            <EntryRow entry={entry} added={added[entry.id]} onEdit={onEdit} />
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
 * 🚨 **등장 애니메이션을 만들지 않는다** (디자인 시스템 §8). 펼침은 즉시다.
 */
function CheckedGroup({
  entries,
  added,
  open,
  onToggle,
  onEdit,
}: {
  entries: PhotoEntry[];
  added: Record<string, AddedEventInput>;
  open: boolean;
  onToggle: () => void;
  onEdit: (entry: PhotoEntry) => void;
}) {
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
            <li key={entry.id}>
              <EntryRow entry={entry} added={added[entry.id]} onEdit={onEdit} />
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
  added,
  onEdit,
}: {
  entry: PhotoEntry;
  /** 이 줄을 캘린더에 넣었다면 **넣은 일시.** 안 넣었으면 `undefined` 다. */
  added?: AddedEventInput;
  onEdit: (entry: PhotoEntry) => void;
}) {
  return (
    <div
      className={cn(
        "rounded-card bg-surface border p-4",
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
        <Button variant="tertiary" size="compact" onClick={() => onEdit(entry)}>
          {editLabel(entry, added !== undefined)}
        </Button>
      </div>

      <dl className="mt-3 flex flex-col gap-1">
        <EntryMeta
          term="언제"
          value={entry.date ? formatDay(entry.date) : "못 읽었어요"}
          missing={entry.date === null}
        />
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
function calendarValue(entry: PhotoEntry, added: AddedEventInput): string {
  const sameDay = entry.date !== null && entry.date === added.date;
  if (!sameDay) return formatEventTime(added.starts_at, added.all_day);
  return added.all_day ? "하루 종일" : formatTimeOfDay(added.starts_at);
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
          맞는 것만 골라주세요. 고르지 않은 태그는 저장하지 않아요.
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

function PreviewRow({ term, value }: { term: string; value: string }) {
  return (
    <div className="flex gap-3">
      {/* 🚨 폭을 고정하지 않는다. 글자를 키우면 라벨이 잘려서 무엇을 저장하는지가 가려진다. */}
      <dt className="text-body-sm text-ink-subtle shrink-0 basis-24">{term}</dt>
      <dd className="text-body-sm text-ink min-w-0">{value}</dd>
    </div>
  );
}
