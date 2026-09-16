"use client";

import { Ruler, Trash2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { ICON_SIZE, ICON_STROKE } from "@/components/ui/icon";
import { IconButton } from "@/components/ui/icon-button";
import { IconTile } from "@/components/ui/icon-tile";
import { formatDay } from "@/lib/format";
import type { GrowthLog } from "@/lib/api/types";

/**
 * 11 키·몸무게 측정 기록 — **줄**이다. 차트도, 카드도 아니다.
 *
 * 🚨 **이 목록의 요점은 없는 것에 있다.** 성장 곡선 · 백분위 · 또래 비교 · "지난번보다 +2cm"
 *    를 만들지 않는다. `DESIGN.md` 의 Don't 가 "부모가 자기 아이를 지표로 보게 하지 않는다"
 *    이고, 증감은 이 제품이 하지 않기로 한 **발달 평가**로 읽힌다 (최상위 CLAUDE.md §2 ·
 *    스펙 아웃). 여기 있는 것은 "언제 무엇을 쟀다" 는 사실 목록뿐이다.
 *
 * 🚨 **줄 문법은 07 기록 줄(`ObservationList`)과 같다** — 왼쪽 타일, 본문 한 줄, 그 아래
 *    메타 한 줄. 같은 앱 안에서 "쌓인 것을 훑는 목록" 은 한 가지 모양이어야 한다.
 *    다른 것은 본문이 부모가 적은 말이 아니라 **잰 값**이라는 것 하나다.
 *
 * 🚨 **타일은 뉴트럴이다.** 도메인 색을 쓰지 않는다 — 도메인 색은 "어느 Agent 결과인가"
 *    (넓혀도 "어느 영역인가") 인데 측정은 Agent 도 영역도 아니다. 브랜드색도 쓰지 않는다
 *    (`IconTile` 주석 — 줄마다 초록 타일이 서면 브랜드가 장식이 된다).
 *
 * 🚨 **날짜 문구를 만들지 않는다.** `measured_label`("2주 전")은 서버 문구다 (CLAUDE.md §3).
 *    없으면 그 자리를 비운다. 절대 날짜(`formatDay`)는 계산이 아니라 표기라서 프론트가 한다
 *    (`lib/format.ts` 머리말).
 */
export function GrowthLogList({
  logs,
  onDelete,
  deletingId,
}: {
  logs: GrowthLog[];
  onDelete: (log: GrowthLog) => void;
  /** 지우는 중인 줄. 그 줄의 버튼만 잠근다 — 목록 전체를 잠그면 다른 줄까지 굳는다. */
  deletingId: string | null;
}) {
  return (
    <ul className="border-line divide-line rounded-card bg-surface divide-y overflow-hidden border">
      {logs.map((log) => (
        <li key={log.id}>
          <GrowthLogRow
            log={log}
            onDelete={() => onDelete(log)}
            deleting={deletingId === log.id}
          />
        </li>
      ))}
    </ul>
  );
}

function GrowthLogRow({
  log,
  onDelete,
  deleting,
}: {
  log: GrowthLog;
  onDelete: () => void;
  deleting: boolean;
}) {
  /**
   * 🚨 **지우기 전에 확인 단계를 둔다. 하지만 승인 게이트가 아니다** — 게이트는 딱 2곳이고
   *    늘리지 않는다 (최상위 §2). 그래서 `btn-approve` 도 `caution` 도 쓰지 않고
   *    `btn-primary` + `btn-tertiary` 다 (디자인 시스템 §7 "교정" 과 같은 처리).
   *
   * 🚨 **빨강을 쓰지 않는다.** 잘못 적은 숫자를 지우는 것은 사고가 아니라 **고치는 행위**다 —
   *    07 에서 `wrong` 교정에 `btn-danger` 를 안 쓴 것과 같은 이유다. `danger` 는
   *    알레르기·건강 중단·파괴적 확정에만 남긴다 (디자인 시스템 §3).
   */
  const [confirming, setConfirming] = useState(false);

  /**
   * 🚨 **확인 패널을 화면 안으로 끌어온다.** 목록 중간의 줄을 누르면 패널이 하단 네비 뒤에
   *    열려서, 부모가 보기에는 아무 일도 안 일어난 것이 된다 (안전 정보 목록과 같은 처리).
   */
  const confirmRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (confirming) confirmRef.current?.scrollIntoView({ block: "nearest" });
  }, [confirming]);

  return (
    <div className="flex flex-col">
      <div className="flex items-start gap-3 px-4 py-3.5">
        <IconTile icon={Ruler} tone="neutral" />

        <div className="flex min-w-0 flex-1 flex-col gap-1">
          {/* 🚨 잰 값이 주어다. 없는 쪽은 자리를 만들지 않는다 —
              "키 -" 를 그리면 안 잰 것이 0 으로 읽힌다. */}
          <p className="text-body text-ink">
            <GrowthValues log={log} />
          </p>
          {/* 🚨 띄운 가운뎃점은 줄당 하나다 (디자인 시스템 §4). */}
          <p className="text-caption text-ink-subtle">
            {formatDay(log.measured_on)}
            {log.measured_label ? ` · ${log.measured_label}` : ""}
          </p>
        </div>

        <IconButton
          label="이 기록 지우기"
          onClick={() => setConfirming((open) => !open)}
          disabled={deleting}
          className="-my-1"
        >
          <Trash2 aria-hidden size={ICON_SIZE.md} strokeWidth={ICON_STROKE} />
        </IconButton>
      </div>

      {confirming ? (
        <div
          ref={confirmRef}
          // 🚨 `scroll-mb-*` 가 없으면 `scrollIntoView` 가 하단 네비 **뒤**를 "보인다" 고
          //    판단해서 버튼이 그대로 가려진다. 네비 높이(51px)보다 넉넉히 준다.
          className="bg-surface-muted border-line flex scroll-mb-28 flex-col gap-3 border-t px-4 py-3.5"
        >
          {/* 🚨 예측을 쓰지 않는다 — 일어날 일만 적는다 (디자인 시스템 §7 "교정"). */}
          <p className="text-body-sm text-ink-muted">
            이 줄이 측정 기록에서 빠져요. 다시 재서 적을 수 있어요.
          </p>
          <div className="flex gap-2">
            <Button size="compact" onClick={onDelete} disabled={deleting} className="flex-1">
              {deleting ? "지우는 중이에요" : "지우기"}
            </Button>
            <Button
              variant="tertiary"
              size="compact"
              onClick={() => setConfirming(false)}
              disabled={deleting}
              className="flex-1"
            >
              그만두기
            </Button>
          </div>
        </div>
      ) : null}
    </div>
  );
}

/**
 * 잰 값 한 줄. 🚨 **한쪽만 잰 날이 있다** — 없는 쪽은 아예 쓰지 않는다.
 * 단위를 숫자에 붙여 쓰는 이유는 "104.2 cm" 가 두 덩이로 읽혀 줄이 흩어지기 때문이다.
 */
function GrowthValues({ log }: { log: GrowthLog }) {
  const parts: string[] = [];
  if (log.height_cm !== null) parts.push(`키 ${log.height_cm}cm`);
  if (log.weight_kg !== null) parts.push(`몸무게 ${log.weight_kg}kg`);

  // 서버가 둘 다 비어 있는 행을 거부하지만, 화면이 그 약속에 기대 빈 줄을 그리지는 않는다.
  if (parts.length === 0) return <span className="text-ink-muted">잰 값이 없어요</span>;

  return <>{parts.join(", ")}</>;
}
