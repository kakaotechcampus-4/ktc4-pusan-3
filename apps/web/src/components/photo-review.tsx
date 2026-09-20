"use client";

import { josa } from "es-hangul";
import { ChevronDown, ChevronUp } from "lucide-react";
import { useState } from "react";

import { PhotoEntrySheet } from "@/components/photo-entry-sheet";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Chip, ChipRow } from "@/components/ui/chip";
import { ICON_SIZE, ICON_STROKE } from "@/components/ui/icon";
import { PhotoCard } from "@/components/ui/photo-card";
import { Spinner } from "@/components/ui/spinner";
import { cn } from "@/lib/cn";
import { formatDay } from "@/lib/format";
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
 * 🚨 **도메인 색을 쓰지 않는다.** lane 은 도메인(`food`·`activity`·`education`·`health`)이
 *    아니다 — 이름이 겹치는 `activity` 가 있지만 여기서는 "아이 활동 사진" 이라는 읽기 방식이다.
 *
 * 🚨 **`raw_text` 는 OCR 로 들어온 외부 텍스트다.** 그대로 텍스트로만 그린다 —
 *    `dangerouslySetInnerHTML` 를 쓰지 않는다 (apps/web/CLAUDE.md §4).
 */

/**
 * 서버 추측이 이 값보다 확실할 때만 **부모가 고른 것과 다르다는 사실**을 화면에 올린다.
 *
 * 🚨 **lane 의 정본은 부모가 시트에서 고른 값이다** (`PhotoSourceSheet`). 서버 추측은 그걸
 *    덮지 않고, 어긋날 때 한 줄로 알리기만 한다.
 * 🚨 **숫자를 화면에 그리지 않는다.** 0.72 를 보여줘 봐야 부모가 할 수 있는 일이 없다.
 */
const SURE_ENOUGH = 0.6;

interface LaneCopy {
  name: string;
  read: string;
  mismatch: (other: string) => string;
  note: string;
  footnote: string;
}

const LANE: Record<PhotoLane, LaneCopy> = {
  document: {
    name: "알림장이나 식단표",
    read: "알림장이나 식단표로 읽었어요.",
    mismatch: (other) => `알림장이나 식단표로 고르셨는데, 글자가 거의 없어요. ${other}일까요?`,
    note: "문서에서는 글자만 읽어요. 준비물과 일시로 정리하고, 아이의 기록으로는 쌓지 않아요.",
    footnote:
      "문서에서 읽은 것은 기관이 알려준 사실로만 저장해요. 아이의 성향이나 선호로 해석하지 않아요.",
  },
  activity: {
    name: "아이 활동 사진",
    read: "아이 활동 사진으로 읽었어요.",
    mismatch: (other) => `아이 활동 사진으로 고르셨는데, 글자가 많아요. ${other}일까요?`,
    note: "사진에서 활동 태그만 뽑아요. 얼굴이나 사람은 분석하지 않아요.",
    footnote: "태그는 성향이나 발달로 확정하지 않아요. 알레르기나 건강 정보로도 쓰지 않아요.",
  },
};

const OTHER_LANE: Record<PhotoLane, PhotoLane> = { document: "activity", activity: "document" };

const ENTRY_KIND_LABEL: Record<PhotoEntry["kind"], string> = {
  event: "일정",
  supply: "준비물",
  meal: "급식",
};

export interface PhotoReviewProps {
  /** 🚨 **부모가 시트에서 고른 값.** 이 화면의 시작점이자 정본이다 (서버 추측이 아니다). */
  declared: PhotoLane;
  /** 서버가 읽은 쪽. 고른 것과 다를 때만 화면에 올린다. 안 왔으면 `null`. */
  guess: PhotoLane | null;
  confidence: number;
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
  declared,
  guess,
  confidence,
  parsed,
  date,
  onCommit,
  committing,
  commitError,
  onGoHome,
  previewUrl,
}: PhotoReviewProps) {
  // 🚨 부모가 고른 것으로 시작한다. 여기서 바꾸면 그쪽이 정본이고 commit 에 그 값이 실린다.
  const [lane, setLane] = useState<PhotoLane>(declared);

  /**
   * 🚨 **읽어낸 항목의 정본은 여기다.** 부모가 고치면 이 배열이 바뀌고, 저장은 이 값을 보낸다 —
   *    서버가 보낸 `parsed` 를 다시 읽지 않는다.
   */
  const [entries, setEntries] = useState<PhotoEntry[]>(() => parsed.entries ?? []);
  const [tags, setTags] = useState<string[]>([]);
  const [editing, setEditing] = useState<PhotoEntry | null>(null);
  const [showChecked, setShowChecked] = useState(false);

  const copy = LANE[lane];
  const other = OTHER_LANE[lane];

  /**
   * 🚨 **서버가 다르게 읽었을 때만 참이다.** 부모가 여기서 lane 을 바꾸면 그 순간 어긋남이
   *    사라지므로 문장이 저절로 확인 문구로 돌아간다.
   */
  const mismatched = guess !== null && guess !== lane && confidence >= SURE_ENOUGH;

  function toggleLane() {
    setLane(other);
    // 🚨 고른 것을 그대로 들고 넘어가지 않는다. 준비물로 읽은 것이 활동 태그로 저장되면
    //    출처(`confidence_source`)가 통째로 뒤바뀐다.
    setTags([]);
  }

  function saveEntry(next: PhotoEntry) {
    setEntries((prev) => prev.map((e) => (e.id === next.id ? next : e)));
    setEditing(null);
  }

  function removeEntry(id: string) {
    setEntries((prev) => prev.filter((e) => e.id !== id));
    setEditing(null);
  }

  const needsReview = entries.filter((e) => e.needs_review);
  const checked = entries.filter((e) => !e.needs_review);

  /**
   * 🚨 **날짜를 만들지 않는다.** 문서는 확인된 항목이 들고 있는 날짜를, 활동은 화면이 들고 온
   *    날짜를 그대로 쓴다 — 없으면 없는 대로 둔다 (apps/web/CLAUDE.md §4).
   */
  const attachDate =
    lane === "document" ? (checked.find((e) => e.date !== null)?.date ?? null) : date;
  const attachToCalendar = attachDate !== null;

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
            <div className="flex flex-wrap items-center justify-between gap-2">
              <p className="text-body text-ink">
                {mismatched ? copy.mismatch(LANE[other].name) : copy.read}
              </p>
              {/* 🚨 "아니에요" 가 아니라 **바뀔 결과**를 버튼에 쓴다.
                  🚨 라벨에 조사를 박지 않는다 — `josa` 가 받침으로 고른다. */}
              <Button variant="tertiary" size="compact" onClick={toggleLane}>
                {josa(LANE[other].name, "이에요/예요")}
              </Button>
            </div>
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
          <NeedsReviewGroup entries={needsReview} onEdit={setEditing} />
          <CheckedGroup
            entries={checked}
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
            <PreviewRow
              term="확인한 것"
              value={checked.length > 0 ? `${checked.length}건` : "아직 없음"}
            />
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

        {/* 🚨 **몇 건이 빠지는지 숫자로 말한다.** 확인 안 한 것이 조용히 사라지면
            "승인 전에는 저장되지 않아요" 의 반대말이 된다. */}
        {lane === "document" && needsReview.length > 0 ? (
          <p className="text-body-sm text-ink-muted bg-surface-muted rounded-field mt-3 px-3 py-2">
            확인하지 않은 {needsReview.length}건은 저장하지 않아요.
          </p>
        ) : null}

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

        <p className="text-caption text-ink-subtle mt-2">
          {attachDate
            ? lane === "document"
              ? `${formatDay(attachDate)} 일정 초안으로 함께 올라가요. 캘린더에 확정하는 것은 그 화면에서 따로 승인해요.`
              : `저장하면 이 사진도 ${formatDay(attachDate)} 캘린더에 함께 남아요.`
            : "읽어낸 일시가 없어서 일정은 만들지 않아요."}
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
  onEdit,
}: {
  entries: PhotoEntry[];
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
            <EntryRow entry={entry} onEdit={onEdit} />
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
  open,
  onToggle,
  onEdit,
}: {
  entries: PhotoEntry[];
  open: boolean;
  onToggle: () => void;
  onEdit: (entry: PhotoEntry) => void;
}) {
  if (entries.length === 0) return null;

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
        <ul id="photo-checked-list" className="flex flex-col gap-2">
          {entries.map((entry) => (
            <li key={entry.id}>
              <EntryRow entry={entry} onEdit={onEdit} />
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}

/* ── 항목 한 줄 ───────────────────────────────────────────────────────── */

function EntryRow({ entry, onEdit }: { entry: PhotoEntry; onEdit: (entry: PhotoEntry) => void }) {
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
          <p className="text-caption text-ink-subtle">{ENTRY_KIND_LABEL[entry.kind]}</p>
          <p className="text-body text-ink mt-0.5">{entry.title}</p>
        </div>
        <Button variant="tertiary" size="compact" onClick={() => onEdit(entry)}>
          고치기
        </Button>
      </div>

      <dl className="mt-3 flex flex-col gap-1">
        <EntryMeta
          term="언제"
          value={entry.date ? formatDay(entry.date) : "못 읽었어요"}
          missing={entry.date === null}
        />
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
