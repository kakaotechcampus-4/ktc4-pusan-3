"use client";

import { josa } from "es-hangul";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Chip, ChipRow } from "@/components/ui/chip";
import { PhotoCard } from "@/components/ui/photo-card";
import { Spinner } from "@/components/ui/spinner";
import { TextInput } from "@/components/ui/text-input";
import { formatDay } from "@/lib/format";
import type { ParsedEvent, PhotoCommitRequest, PhotoLane } from "@/lib/api";

/**
 * 08 사진으로 적기 — 읽어낸 것을 보호자가 확인하는 칸.
 *
 * 🚨 **여기는 승인 게이트가 아니다.** 문서 lane 이 만드는 `event` 는 `draft` 고, 캘린더에 쓰는
 *    되돌릴 수 없는 지점은 여전히 `POST /events/{eid}/confirm` 하나다 (CLAUDE.md §2 —
 *    승인 게이트를 늘리지도 줄이지도 않는다). 그래서 `btn-approve` 도 `caution` 도 쓰지 않는다.
 *    "아직 저장하지 않았어요" 는 경고가 아니라 **사실**이라 중립 면(`surface-muted`)에 앉힌다.
 *    ⚠️ 디자인 시스템 v1 §7 `card-photo` · §11 표가 여기에 `caution` 계열을 적어 뒀는데,
 *       최상위 §2 와 어긋나서 이 PR 에서 문서 쪽을 함께 고쳤다 (apps/web/CLAUDE.md §5 절차).
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
 *    덮지 않고, 어긋날 때 한 줄로 알리기만 한다 — 어설픈 추측으로 부모가 선언한 것을
 *    되묻으면 "무엇을 찍었는지" 를 찍은 사람보다 모델이 더 잘 안다는 화면이 된다.
 * 🚨 **숫자를 화면에 그리지 않는다.** 0.72 를 보여줘 봐야 부모가 할 수 있는 일이 없고,
 *    수치를 늘어놓는 화면을 만들지 않는다는 규칙(DESIGN.md Don't)과도 어긋난다.
 */
const SURE_ENOUGH = 0.6;

interface LaneCopy {
  /** 이 lane 이 무엇인지. 바꾸는 버튼의 문구를 만들 때도 쓴다. */
  name: string;
  /** 부모가 고른 대로 읽었다는 확인 한 줄. */
  read: string;
  /** 🚨 서버가 **다르게** 읽었을 때만 선다. `other` 는 서버가 읽은 쪽이다. */
  mismatch: (other: string) => string;
  note: string;
  itemsTitle: string;
  /**
   * 저장 미리보기의 항목 줄에 서는 말. 🚨 `itemsTitle` 을 그대로 쓰지 않는다 — 미리보기는
   * 라벨과 값이 두 칸으로 서는 표라, "뽑아낸 활동 태그" 처럼 긴 말이 들어가면 라벨이 두 줄로
   * 접히면서 옆 칸의 값과 기준선이 어긋난다.
   */
  previewTerm: string;
  itemsHint: string;
  /** 저장 미리보기에서 "출처" 줄에 서는 말. 계약서의 `confidence_source` 를 사람 말로 옮긴 것. */
  source: string;
  saveLabel: string;
  /** 저장 버튼 아래 한 줄. 캘린더에 무엇이 어떻게 들어가는지. */
  attachNote: (dayLabel: string) => string;
  footnote: string;
}

const LANE: Record<PhotoLane, LaneCopy> = {
  document: {
    name: "알림장이나 식단표",
    read: "알림장이나 식단표로 읽었어요.",
    mismatch: (other) => `알림장이나 식단표로 고르셨는데, 글자가 거의 없어요. ${other}일까요?`,
    note: "문서에서는 글자만 읽어요. 준비물과 일시로 정리하고, 아이의 기록으로는 쌓지 않아요.",
    itemsTitle: "준비물",
    previewTerm: "준비물",
    itemsHint: "고른 것만 저장해요.",
    source: "기관이 보낸 문서",
    saveLabel: "이 내용으로 저장",
    attachNote: (dayLabel) =>
      `${dayLabel} 일정 초안으로 함께 올라가요. 캘린더에 확정하는 것은 그 화면에서 따로 승인해요.`,
    footnote:
      "문서에서 읽은 것은 기관이 알려준 사실로만 저장해요. 아이의 성향이나 선호로 해석하지 않아요.",
  },
  activity: {
    name: "아이 활동 사진",
    read: "아이 활동 사진으로 읽었어요.",
    mismatch: (other) => `아이 활동 사진으로 고르셨는데, 글자가 많아요. ${other}일까요?`,
    note: "사진에서 활동 태그만 뽑아요. 얼굴이나 사람은 분석하지 않아요.",
    itemsTitle: "뽑아낸 활동 태그",
    previewTerm: "활동",
    itemsHint: "맞는 것만 골라주세요. 고르지 않은 태그는 저장하지 않아요.",
    source: "사진 태그 · 보호자 확인",
    saveLabel: "이 활동으로 저장",
    attachNote: (dayLabel) => `저장하면 이 사진도 ${dayLabel} 캘린더에 함께 남아요.`,
    footnote: "태그는 성향이나 발달로 확정하지 않아요. 알레르기나 건강 정보로도 쓰지 않아요.",
  },
};

const OTHER_LANE: Record<PhotoLane, PhotoLane> = { document: "activity", activity: "document" };

/**
 * 🚨 **문서와 활동에서 미리 고르는 기준이 다르다.**
 *
 * 문서 준비물은 기관이 적어 준 글자를 옮긴 것이라 **미리 골라 두고** 부모가 틀린 것을 뺀다.
 * 활동 태그는 모델이 **아이에 대해 추측한 것**이라 하나도 고르지 않은 채로 시작한다 —
 * 한 번의 관찰을 성향으로 확정하지 않는다는 규칙(CLAUDE.md §2)이 화면에서 지켜지려면,
 * 아이에 대한 말은 부모가 눌러서 켜야 한다. 확인 없이 한 번 누르면 전부 저장되는 화면은
 * "보호자 확인" 이라는 출처 표기를 거짓말로 만든다.
 */
function initialSelection(lane: PhotoLane, items: string[]): string[] {
  return lane === "document" ? items : [];
}

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
  /** 힌트 한 줄로 다시 읽기 (`POST /photo-runs/{rid}/reanalyze`). */
  onReanalyze: (hint: string) => void;
  reanalyzing: boolean;
  onPickAnother: () => void;
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
  onReanalyze,
  reanalyzing,
  onPickAnother,
  previewUrl,
}: PhotoReviewProps) {
  // 🚨 부모가 고른 것으로 시작한다. 여기서 바꾸면 그쪽이 정본이고 commit 에 그 값이 실린다.
  const [lane, setLane] = useState<PhotoLane>(declared);
  const [selected, setSelected] = useState<string[]>(() =>
    initialSelection(declared, parsed.extracted.items),
  );
  const [hintOpen, setHintOpen] = useState(false);
  const [hint, setHint] = useState("");

  const copy = LANE[lane];
  const other = OTHER_LANE[lane];
  const items = parsed.extracted.items;

  /**
   * 🚨 **날짜를 만들지 않는다.** 문서는 서버가 읽어낸 `when` 을, 활동은 화면이 들고 온 날짜를
   *    그대로 쓴다 — 없으면 없는 대로 두고 오늘로 채우지 않는다 (apps/web/CLAUDE.md §4).
   */
  const attachDate = lane === "document" ? (parsed.extracted.when ?? null) : date;
  const attachToCalendar = attachDate !== null;

  /**
   * 🚨 **서버가 다르게 읽었을 때만 참이다.** 부모가 여기서 lane 을 바꾸면 그 순간 어긋남이
   *    사라지므로 (`lane` 이 `guess` 와 같아진다) 문장이 저절로 확인 문구로 돌아간다.
   */
  const mismatched = guess !== null && guess !== lane && confidence >= SURE_ENOUGH;

  function toggleLane() {
    setLane(other);
    // 🚨 고른 것을 그대로 들고 넘어가지 않는다. 준비물로 고른 "수건" 이 활동 태그로 저장되면
    //    출처(`confidence_source`)가 통째로 뒤바뀐다 — lane 마다 미리 고르는 기준도 다르다.
    setSelected(initialSelection(other, items));
  }

  function toggleItem(item: string) {
    setSelected((prev) => (prev.includes(item) ? prev.filter((v) => v !== item) : [...prev, item]));
  }

  /**
   * 🚨 **활동 lane 은 태그를 하나도 안 고르면 저장할 수 없다.** 사진은 날짜가 있으니 캘린더에
   *    붙일 수는 있지만, 그 상태로 "이 활동으로 저장" 이 눌리면 화면이 바로 위에서 한
   *    "고르지 않은 태그는 저장하지 않아요" 를 스스로 뒤집는다 (실제로 눌렸다).
   *    문서 lane 은 다르다 — 준비물을 다 빼도 읽어낸 일시로 일정 초안은 남길 수 있다.
   */
  const hasSomethingToSave =
    lane === "activity" ? selected.length > 0 : selected.length > 0 || attachToCalendar;
  const canSave = !committing && hasSomethingToSave;

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
              {/* 🚨 "아니에요" 가 아니라 **바뀔 결과**를 버튼에 쓴다. 한 번 눌렀을 때 무엇이
                  되는지가 버튼에 없으면 부모가 눌러 보고 나서야 안다. */}
              {/* 🚨 라벨에 조사를 박지 않는다 — `josa` 가 받침으로 고른다 (apps/web/CLAUDE.md §3).
                  "식단표예요" 와 "사진이에요" 가 갈리는 자리다. */}
              <Button variant="tertiary" size="compact" onClick={toggleLane}>
                {josa(LANE[other].name, "이에요/예요")}
              </Button>
            </div>
            <p className="text-body-sm text-ink-muted">{copy.note}</p>

            {lane === "document" && parsed.raw_text ? (
              <div className="border-line border-t pt-3">
                <p className="text-caption text-ink-subtle">인식한 원문</p>
                {/* 🚨 외부 텍스트다. 텍스트로만 그린다 — `whitespace-pre-line` 은 줄바꿈을
                    살릴 뿐이고 마크업을 해석하지 않는다. */}
                <p className="text-body-sm text-ink mt-1 whitespace-pre-line">{parsed.raw_text}</p>
              </div>
            ) : null}
          </div>
        }
      >
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src={previewUrl} alt="방금 넣은 사진" className="h-full w-full object-cover" />
      </PhotoCard>

      <section className="flex flex-col gap-2">
        <div>
          <h2 className="text-label text-brand">{copy.itemsTitle}</h2>
          <p className="text-body-sm text-ink-muted mt-1">{copy.itemsHint}</p>
        </div>
        {items.length > 0 ? (
          <ChipRow>
            {items.map((item) => (
              <Chip
                key={item}
                selected={selected.includes(item)}
                disabled={committing}
                onClick={() => toggleItem(item)}
              >
                {item}
              </Chip>
            ))}
          </ChipRow>
        ) : (
          // 🚨 빈 배열을 숨기지 않는다. 못 읽었으면 못 읽었다고 말하고, 고치는 길은 아래에 있다.
          <p className="text-body-sm text-ink-muted">
            이 사진에서는 {josa(copy.itemsTitle, "을/를")} 읽어내지 못했어요.
          </p>
        )}
      </section>

      <Card>
        <p className="text-section text-ink">이렇게 저장할게요</p>
        {/* 🚨 중립 면이다. 사실을 말하는 자리라 경고색을 쓰지 않는다 (위 주석). */}
        <p className="text-body-sm text-ink-muted bg-surface-muted rounded-field mt-2 px-3 py-2">
          지금까지는 아무것도 저장되지 않았어요. 아래를 눌러야 저장돼요.
        </p>

        <dl className="mt-4 flex flex-col gap-2">
          <PreviewRow
            term={copy.previewTerm}
            value={selected.length > 0 ? selected.join(", ") : "고른 것 없음"}
          />
          {attachDate ? (
            <PreviewRow
              term={lane === "document" ? "일시" : "남기는 날"}
              value={
                lane === "document" && parsed.extracted.all_day
                  ? `${formatDay(attachDate)} 하루 종일`
                  : formatDay(attachDate)
              }
            />
          ) : null}
          <PreviewRow term="출처" value={copy.source} />
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

        <Button
          block
          className="mt-4"
          disabled={!canSave}
          aria-busy={committing}
          onClick={() =>
            onCommit({ lane, selected_items: selected, attach_to_calendar: attachToCalendar })
          }
        >
          {committing ? <Spinner /> : null}
          {committing ? "저장하는 중이에요" : copy.saveLabel}
        </Button>

        <p className="text-caption text-ink-subtle mt-2">
          {attachDate
            ? copy.attachNote(formatDay(attachDate))
            : "읽어낸 일시가 없어서 일정은 만들지 않아요."}
        </p>
      </Card>

      <section className="flex flex-col gap-2">
        <h2 className="text-label text-brand">빠진 게 있거나 잘못 읽었나요?</h2>

        {hintOpen ? (
          <Card className="flex flex-col gap-3">
            <TextInput
              label="빠진 것을 알려주세요"
              hint="알려주신 내용은 다시 읽을 때 참고만 하고, 그대로 저장하지 않아요."
              placeholder="예: 물병도 있었어요"
              value={hint}
              disabled={reanalyzing}
              onChange={(e) => setHint(e.target.value)}
            />
            <div className="flex flex-wrap gap-2">
              <Button
                disabled={hint.trim().length === 0 || reanalyzing}
                aria-busy={reanalyzing}
                onClick={() => onReanalyze(hint.trim())}
              >
                {reanalyzing ? <Spinner /> : null}
                {reanalyzing ? "다시 읽는 중이에요" : "다시 읽기"}
              </Button>
              <Button
                variant="secondary"
                disabled={reanalyzing}
                onClick={() => {
                  setHintOpen(false);
                  setHint("");
                }}
              >
                취소
              </Button>
            </div>
          </Card>
        ) : (
          <div className="flex flex-wrap gap-2">
            <Button variant="secondary" onClick={() => setHintOpen(true)}>
              빠진 것을 알려주고 다시 읽기
            </Button>
            {/* 🚨 "폐기" 라고 쓰지 않는다. 아직 저장된 것이 없어서 폐기할 것도 없다 —
                하는 일은 사진을 바꾸는 것뿐이고, 문구가 그 사실과 같은 말을 해야 한다. */}
            <Button variant="tertiary" onClick={onPickAnother}>
              다른 사진 고르기
            </Button>
          </div>
        )}
      </section>

      <p className="text-caption text-ink-subtle">{copy.footnote}</p>
    </div>
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
