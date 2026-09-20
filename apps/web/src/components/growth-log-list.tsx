"use client";

import { ChevronRight, Ruler, Trash2 } from "lucide-react";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { ICON_SIZE, ICON_STROKE } from "@/components/ui/icon";
import { IconButton } from "@/components/ui/icon-button";
import { IconTile } from "@/components/ui/icon-tile";
import { cn } from "@/lib/cn";
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
  scrollable = false,
}: {
  logs: GrowthLog[];
  onDelete: (log: GrowthLog) => void;
  /** 지우는 중인 줄. 그 줄의 버튼만 잠근다 — 목록 전체를 잠그면 다른 줄까지 굳는다. */
  deletingId: string | null;
  /**
   * 목록 자체를 스크롤 영역으로 만든다 (11-1 상세). 🚨 **기본값은 꺼짐**이다 — 프로필(11)은
   * 한 줄만 세우고, 한 줄짜리 목록에 스크롤 상자를 두면 잘린 것처럼 보인다.
   *
   * 🚨 `overflow-hidden` 을 **대체**한다. 둘을 같이 주면 `cn()` 이 tailwind-merge 가 아니라서
   *    어느 쪽이 이길지가 생성된 CSS 순서에 달린다 (이 파일 위아래에서 여러 번 덴 자리다).
   *    `overflow-y-auto` 는 가로축도 `auto` 로 계산돼서 모서리 둥글림은 그대로 잘린다.
   *
   * 🚨 **`tabindex` 를 붙이지 않는다.** 줄마다 지우기 버튼이 있어서 키보드는 그 버튼을 타고
   *    넘어가며 영역이 따라 스크롤된다 — 상자 자체에 탭 정지를 하나 더 만들 이유가 없다.
   */
  scrollable?: boolean;
}) {
  return (
    <ul
      className={cn(
        "border-line divide-line rounded-card bg-surface divide-y border",
        /* 🚨 높이를 `rem` 으로 고정하지 않는다 — 줄 높이가 글자 크기를 따라 늘어나는데
           (`min-h-touch` · 디자인 시스템 §10) 상자만 고정이면 200% 확대에서 두 줄만 보인다.
           화면 높이의 60% 면 844px 기기에서 여섯 줄 남짓이고, 마지막 줄이 반쯤 잘려서
           "더 있다" 를 모양이 말한다 — 스크롤 안내 문구를 따로 두지 않는 이유다. */
        scrollable ? "max-h-[60vh] overflow-y-auto" : "overflow-hidden",
      )}
    >
      {logs.map((log) => (
        <li key={log.id}>
          <GrowthLogRow log={log} onDelete={() => onDelete(log)} deleting={deletingId === log.id} />
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
   *    늘리지 않는다 (최상위 §2). 그래서 `btn-approve` 도 `caution` 도 쓰지 않는다.
   *
   * 🚨 **07 교정과 달리 `btn-secondary` 다** (07 은 `btn-primary` + `btn-tertiary`).
   *    이유는 이 패널이 앉는 화면이 다르기 때문이다 — 07 에는 화면 primary 가 없지만
   *    11 에는 "고친 것 저장하기" 가 있고, 이 패널은 **인라인**이라 그 버튼과 한 화면에
   *    같이 선다. 둘 다 채운 초록이면 "한 화면에 primary 하나"(디자인 시스템 §7)가 깨지고,
   *    부모가 다음 행동을 판단해야 한다. 시트(`GrowthSheet`)는 모달이라 뒤가 `inert` 이므로
   *    자기 primary 를 갖는다 — **인라인이냐 모달이냐가 가르는 선**이다.
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
            <Button
              variant="secondary"
              size="compact"
              onClick={onDelete}
              disabled={deleting}
              className="flex-1"
            >
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

/**
 * 11 프로필의 "키 · 몸무게" 구역(코드에서는 `growth`)이 평소에 세우는 것 — **가장 최근에 잰 한 줄**이다.
 *
 * 🚨 **여기서 목록 전체를 펼치지 않는다.** 프로필은 부르는 이름 · 키·몸무게 · 알레르기 셋이
 *    사는 화면이고, 측정 기록은 쌓일수록 길어져서 그냥 두면 한 구역이 다른 둘을 화면 밖으로
 *    민다. 매일 여는 화면에서 이 구역이 답해야 하는 것은 "지금 얼마"지 "그동안 어땠나" 가
 *    아니다 — 후자는 눌러서 가는 11-1 상세가 진다.
 *
 * 🚨 **잰 값을 큰 글자로 세우지 않는다.** 줄 문법(`GrowthLogRow`)을 그대로 쓴다. 값이
 *    `display` 로 서는 순간 그건 지표 타일이고, `DESIGN.md` 의 Don't 가 막는 그것이다.
 *
 * 🚨 **줄 자체가 링크다.** 옆에 "전체 보기" 글자 버튼을 따로 달면 구역 머리줄의 `Plus` 와
 *    합쳐 이 작은 구역에 행동이 셋이 된다 (머리줄 버튼을 아이콘으로 내린 것과 같은 이유).
 */
export function GrowthLatestCard({ log, href }: { log: GrowthLog; href: string }) {
  return (
    <Link
      href={href}
      className="rounded-card border-line bg-surface ease-standard active:bg-surface-muted hover:bg-surface-muted min-h-touch flex items-start gap-3 border px-4 py-3.5 transition-colors duration-120"
    >
      <IconTile icon={Ruler} tone="neutral" />

      <span className="flex min-w-0 flex-1 flex-col gap-1">
        <span className="text-body text-ink">
          <GrowthValues log={log} />
        </span>
        {/* 🚨 띄운 가운뎃점은 줄당 하나다 (디자인 시스템 §4). */}
        <span className="text-caption text-ink-subtle">
          {formatDay(log.measured_on)}
          {log.measured_label ? ` · ${log.measured_label}` : ""}
        </span>
      </span>

      {/* 🚨 화살표가 단독 신호가 되지 않게 이름을 함께 내보낸다 — 줄의 글자는 잰 값이라
          여기를 누르면 어디로 가는지는 말해 주지 않는다. */}
      <span className="sr-only">지난 기록과 변화 보기</span>
      <ChevronRight
        aria-hidden
        size={ICON_SIZE.md}
        strokeWidth={ICON_STROKE}
        className="text-ink-subtle mt-1.5 shrink-0"
      />
    </Link>
  );
}
