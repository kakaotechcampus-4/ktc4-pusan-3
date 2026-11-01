"use client";

import { CircleHelp } from "lucide-react";
import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { ICON_SIZE, ICON_STROKE } from "@/components/ui/icon";
import { IconButton } from "@/components/ui/icon-button";
import { cn } from "@/lib/cn";
import { useMarkTourSeen, useSeenTours } from "@/lib/tour/seen";
import { contentSteps, liveSteps, type TourMode } from "@/lib/tour/steps";
import type { TourDefinition, TourStep } from "@/lib/tour/types";
import { useTourStore } from "@/stores/tour";

/**
 * 화면 투어 — 화면의 한 곳만 밝게 두고 나머지를 덮은 채 단계별로 짚어 준다 (#632).
 * 규칙 원문은 `docs/web/screen-tour-v1.md`. 단계는 `lib/tour/screens/` 에 화면마다 있다.
 *
 * 쓰는 법 — 화면이 두 개를 그린다.
 * - 헤더 오른쪽 끝에 `<TourHelpButton tour={…} />`
 * - 아무 데나 `<ScreenTour tour={…} ready={…} />`. `ready` 는 "비출 것이 다 그려졌다" 이다
 *   (받는 중 · 진행 중에는 `false`). 처음 들어왔고 아직 안 봤으면 `ready` 가 켜질 때 저절로 뜬다.
 *
 * 🚨 **네이티브 `<dialog>` 위에 얹는다** (`BottomSheet` 와 같은 이유). 포커스 가두기 · Esc · 뒤의 `inert` 를
 *    브라우저가 준다. 덮개가 화면 전체의 터치를 받으므로 **비춘 요소도 눌리지 않는다** — "확인하고 넣기" 를
 *    설명하다 그게 눌려 승인 시트가 열리면 안 된다 (§2).
 * 🚨 **바깥을 눌러도 닫히지 않는다.** 닫는 길은 말풍선의 그만 볼래요 · 다 봤어요 · Esc 셋이다.
 * 🚨 **비출 요소가 없으면 그 단계를 뺀다.** 예시 데이터로 채우지 않는다 — 기록이 없는데 기록 카드를 띄우면
 *    우리 아이를 아는 척하는 화면이 된다 (최상위 CLAUDE.md §2 기억).
 * 🚨 **모양은 디자인 시스템 §7 "화면 투어" 다.** 덮개는 시트 뒤와 같은 `scrim`, 말풍선은 그림자 없이 `line`
 *    테두리, 브랜드색은 "다음" 버튼(primary) 하나다. `caution` 은 승인 게이트 전용이라 투어가 주황이면
 *    "확인해야 할 것" 으로 읽힌다 (디자인 시스템 §3).
 * 🚨 **단계 사이는 페이드 교체다.** 밝은 구멍이 다음 요소 크기로 늘어나며 옮겨가지 않는다 —
 *    크기를 움직이지 않는다 (디자인 시스템 §8). 시간은 `--motion-swap` 토큰을 읽는다.
 * ⚠️ **Android 뒤로가기는 아직 투어를 닫지 못한다.** 셸이 웹에 뒤로가기를 넘길 창구가 없어서다
 *    (`apps/mobile/src/native/back-press.ts`). 하위 화면에서는 화면째 돌아가고(투어는 안 본 채로 남는다),
 *    탭 화면에서는 종료 확인 토스트가 뜬다 (§5).
 */

/** 구멍이 요소보다 바깥으로 나오는 여백. 테두리 2px 이 요소 가장자리를 덮지 않게 한다. */
const HOLE_PAD = 6;
/** 구멍과 말풍선 사이. */
const TIP_GAP = 12;
/** 화면 가장자리와 말풍선 사이 (`Screen` 의 좌우 여백과 같다). */
const EDGE = 16;
/** 단계 교체 페이드 시간. 🚨 숫자를 적지 않고 `--motion-swap` 을 읽는다 (`lib/motion.ts` 머리말과 같은 이유). */
function swapMs(): number {
  const raw = getComputedStyle(document.documentElement).getPropertyValue("--motion-swap").trim();
  const value = Number.parseFloat(raw);
  if (!Number.isFinite(value)) return 0;
  return raw.endsWith("ms") ? value : raw.endsWith("s") ? value * 1000 : value;
}

interface Rect {
  top: number;
  left: number;
  width: number;
  height: number;
  /** 구멍의 모서리. 요소가 둥글면(아이콘 버튼 · 채팅바) 그 모양을 따라가고, 아니면 카드 모서리다. */
  radius: string;
}

/** 🚨 DOM 에 있어도 그려지지 않았으면(숨김 · 접힘) 없는 것으로 친다. */
function drawnIn(root: ParentNode, target: string): HTMLElement[] {
  return [...root.querySelectorAll<HTMLElement>(`[data-tour="${target}"]`)].filter(
    (el) => el.getClientRects().length > 0,
  );
}

function findTarget(step: TourStep): HTMLElement | null {
  // `within` 이 있으면 그 범위의 마지막 것(04 의 가장 최근 턴) 안에서만 찾는다 (`TourStep.within`).
  const scope = step.within ? drawnIn(document, step.within).at(-1) : document;
  return scope ? (drawnIn(scope, step.target)[0] ?? null) : null;
}

const isDrawn = (step: TourStep) => findTarget(step) !== null;

/** 그 단계의 요소를 감쌀 구멍. 요소가 없으면 `null` — 덮개만 깔린다. */
function holeOf(step: TourStep): Rect | null {
  const el = findTarget(step);
  if (!el) return null;
  const box = el.getBoundingClientRect();
  // 🚨 반지름은 요소의 것에 여백을 더한다. 동그란 버튼을 네모난 구멍으로 비추면 다른 것을 가리키는 듯 보인다.
  const own = Number.parseFloat(getComputedStyle(el).borderTopLeftRadius) || 0;
  return {
    top: box.top - HOLE_PAD,
    left: box.left - HOLE_PAD,
    width: box.width + HOLE_PAD * 2,
    height: box.height + HOLE_PAD * 2,
    radius: own > 0 ? `${Math.min(own, box.height / 2) + HOLE_PAD}px` : "var(--radius-card)",
  };
}

/**
 * 화면 전환(View Transition) 중인가. 🚨 전환 중에 띄우지 않는다 — 03 홈 → 04 대화처럼 화면이 밀려 들어오는
 * 동안 열면, 아직 움직이는 화면 위에 구멍이 잠깐 서서 엉뚱한 곳을 짚는 것처럼 보인다 (사용자 피드백 · 실서버에서
 * 전환 450ms 중 320ms 에 열렸다). 선택자를 모르는 브라우저는 전환 중이 아닌 것으로 본다.
 */
function inViewTransition(): boolean {
  try {
    return document.documentElement.matches(":active-view-transition");
  } catch {
    return false;
  }
}

/** 자동으로 띄우기 전에 비출 것이 그려지기를 기다리는 최대 시간과 간격. 화면 전환(`--motion-screen`)보다 길어야 한다. */
const AUTO_WAIT_MS = 1500;
const AUTO_POLL_MS = 100;

export function TourHelpButton({ tour }: { tour: TourDefinition }) {
  const start = useTourStore((s) => s.start);
  return (
    <IconButton label="이 화면 설명 보기" data-tour="help" onClick={() => start(tour.key, "help")}>
      <CircleHelp aria-hidden size={ICON_SIZE.md} strokeWidth={ICON_STROKE} />
    </IconButton>
  );
}

export function ScreenTour({ tour, ready }: { tour: TourDefinition; ready: boolean }) {
  const active = useTourStore((s) => s.active);
  const start = useTourStore((s) => s.start);
  const stop = useTourStore((s) => s.stop);
  const seen = useSeenTours();
  const markSeen = useMarkTourSeen();

  // 처음 들어왔고 아직 안 봤으면 저절로 연다.
  useEffect(() => {
    if (tour.auto === false) return;
    if (!ready || seen === undefined || seen.has(tour.key)) return;
    /**
     * 🚨 **받은 즉시 열지 않는다.** 받은 내용은 `Swap` 이 스켈레톤을 내리고 나서야 그려져서, `ready` 가 켜진
     *    순간에는 비출 것이 아직 DOM 에 없다 (실서버 05 에서 그랬다). 그려질 때까지 잠깐 기다리고,
     *    끝내 없으면 열지 않는다 — 본 것으로도 치지 않아서 다음에 내용이 있을 때 뜬다.
     */
    const startedAt = Date.now();
    const timer = setInterval(() => {
      const waited = Date.now() - startedAt;
      if (useTourStore.getState().active || waited > AUTO_WAIT_MS) {
        clearInterval(timer);
        return;
      }
      // 🚨 시트가 열려 있으면 띄우지 않는다 — 시트 위에 덮개가 얹히면 시트를 가리키는 설명이 된다.
      if (document.querySelector("dialog[open]")) return;
      if (inViewTransition()) return;
      if (contentSteps(tour, seen, isDrawn).length === 0) return;
      clearInterval(timer);
      start(tour.key, "auto");
    }, AUTO_POLL_MS);
    return () => clearInterval(timer);
  }, [ready, seen, tour, start]);

  // 🚨 화면을 떠나면 놓는다. 들고 있으면 다음 화면의 투어가 "이미 떠 있다" 로 읽고 안 뜬다.
  useEffect(
    () => () => {
      if (useTourStore.getState().active?.key === tour.key) useTourStore.getState().stop();
    },
    [tour.key],
  );

  const finish = useCallback(() => {
    // 🚨 그만 봐도 본 것이다 (§2). ? 로 연 투어도 끝까지 보거나 그만 보면 본 것으로 친다.
    markSeen(tour.key);
    stop();
  }, [markSeen, stop, tour.key]);

  if (active?.key !== tour.key) return null;
  return (
    <TourOverlay
      key={active.mode}
      tour={tour}
      mode={active.mode}
      seen={seen}
      onDone={finish}
      onEmpty={stop}
    />
  );
}

function TourOverlay({
  tour,
  mode,
  seen,
  onDone,
  onEmpty,
}: {
  tour: TourDefinition;
  mode: TourMode;
  seen: ReadonlySet<string> | undefined;
  onDone: () => void;
  /** 비출 것이 없어 열지 못했다. 본 것으로 치지 않는다. */
  onEmpty: () => void;
}) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const tipRef = useRef<HTMLDivElement>(null);
  const headingRef = useRef<HTMLHeadingElement>(null);
  /** 정리 효과가 스스로 닫는 중인가 (`onClose` 가 그 닫힘을 그만 보기로 세지 않게). */
  const closingRef = useRef(false);
  const titleId = useId();
  const bodyId = useId();

  // 🚨 열 때 한 번 고른다 (`liveSteps` 머리말). `useState` 초기값이라 이 투어가 떠 있는 동안 그대로다.
  const [steps] = useState(() => liveSteps(tour, mode, seen, isDrawn));
  /** 가려는 단계. 말풍선이 사라진 뒤에 `view` 가 따라온다 (아래 단계 교체). */
  const [index, setIndex] = useState(0);
  /** 지금 그려진 단계. 🚨 `index` 를 바로 그리지 않는다 — 말풍선이 사라지는 동안 글자가 먼저 바뀌어 보인다. */
  const [view, setView] = useState(0);
  const [rect, setRect] = useState<Rect | null>(null);
  const [tipTop, setTipTop] = useState<number | null>(null);
  /** 말풍선이 보이는가. 단계를 넘길 때마다 잠깐 꺼진다. */
  const [shown, setShown] = useState(false);
  /** 덮개가 깔렸는가. 처음 한 번 켜지고 닫힐 때까지 꺼지지 않는다 (아래 단계 교체). */
  const [opened, setOpened] = useState(false);
  const openedRef = useRef(false);

  const step = steps[view];
  const last = view === steps.length - 1;

  // 비출 것이 하나도 없으면 띄울 것도 없다. 🚨 본 것으로 치지 않는다 — 내용이 생기면 그때 보여야 한다.
  useEffect(() => {
    if (steps.length === 0) onEmpty();
  }, [steps.length, onEmpty]);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog || steps.length === 0) return;
    closingRef.current = false;
    if (!dialog.open) dialog.showModal();
    // 시트와 같다 — showModal 이 바깥 조작은 막지만 배경 스크롤까지 막지는 않는다.
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = previous;
      // 🚨 우리가 닫는다는 표식을 먼저 세운다 — 이 `close()` 가 낸 `close` 이벤트를 아래 `onClose` 가 그만 보기로 세면
      //    화면을 떠난 것이 "봤음" 이 된다 (§2). 지금은 언마운트로 핸들러가 떨어져 안 불리지만, 거기에 기대지 않는다.
      closingRef.current = true;
      if (dialog.open) dialog.close();
    };
  }, [steps.length]);

  /** 지금 그려진 단계의 요소를 잰다 (창 · 요소 크기가 바뀔 때). */
  const measure = useCallback(() => {
    if (step) setRect(holeOf(step));
  }, [step]);

  /** 다음 · 이전. 말풍선만 먼저 감춘다(여기) → 옮기고 · 재고 → 새 자리에서 보인다(아래 효과). */
  function goTo(next: number) {
    // 🚨 지금 그려진 단계(`view`)가 아니라 가려는 단계(`index`)에서 센다 — 말풍선이 바뀌기 전에 한 번 더 누르면
    //    같은 단계로 다시 가려다 멈췄다 (e2e 에서 그랬다). 빠르게 누르면 그만큼 넘어간다.
    const clamped = Math.min(Math.max(next, 0), steps.length - 1);
    if (clamped === index) return;
    setShown(false);
    setIndex(clamped);
  }

  /**
   * 단계 교체. 🚨 **덮개는 그대로 두고 말풍선만 페이드한다.** 예전에는 구멍까지 투명하게 했는데, 덮개가 그 구멍의
   *    그림자라 화면 전체가 단계마다 밝아졌다 어두워졌다 (깜빡임 · 사용자 피드백). 구멍은 말풍선이 사라진 사이에
   *    다음 요소로 바로 옮긴다 — 크기를 움직이며 옮기지 않는다 (디자인 시스템 §8).
   */
  useEffect(() => {
    const target = steps[index];
    if (!target) return;
    const timer = setTimeout(
      () => {
        // 🚨 부드럽게 스크롤하지 않는다 — 04 대화가 맨 아래로 내릴 때와 같은 이유다 (디자인 시스템 §8).
        findTarget(target)?.scrollIntoView({ block: "center", behavior: "instant" });
        setRect(holeOf(target));
        setView(index);
        setShown(true);
        setOpened(true);
        openedRef.current = true;
        headingRef.current?.focus();
      },
      // 처음 여는 순간은 기다릴 말풍선이 없다. 🚨 ref 로 본다 — `opened` 를 의존성에 넣으면 처음 연 뒤 그것만
      // 바뀌었다고 같은 단계를 한 번 더 그린다.
      openedRef.current ? swapMs() : 0,
    );
    return () => clearTimeout(timer);
  }, [steps, index]);

  // 🚨 창 크기만 보지 않는다 — 늦게 오는 글꼴 · 이미지나 들어오는 움직임으로 요소가 밀려도 구멍이 따라가야 한다.
  useEffect(() => {
    const el = step ? findTarget(step) : null;
    const observer = new ResizeObserver(() => measure());
    observer.observe(document.body);
    if (el) observer.observe(el);
    window.addEventListener("resize", measure);
    return () => {
      observer.disconnect();
      window.removeEventListener("resize", measure);
    };
  }, [measure, step]);

  // 말풍선은 구멍 아래가 비었으면 아래, 아니면 위. 둘 다 안 되면(요소가 화면만큼 크면) 화면 아래에 얹는다.
  useLayoutEffect(() => {
    const tip = tipRef.current;
    if (!tip || !rect) return;
    const height = tip.offsetHeight;
    const viewport = window.innerHeight;
    const below = rect.top + rect.height + TIP_GAP;
    const above = rect.top - TIP_GAP - height;
    if (below + height <= viewport - EDGE) setTipTop(below);
    else if (above >= EDGE) setTipTop(above);
    else setTipTop(Math.max(EDGE, viewport - height - EDGE));
  }, [rect, view]);

  if (!step) return null;

  return (
    <dialog
      ref={dialogRef}
      aria-labelledby={titleId}
      aria-describedby={bodyId}
      onCancel={(e) => {
        // Esc 는 그만 보기다. 브라우저가 직접 닫게 두지 않는다 — 닫힘을 이 컴포넌트가 센다.
        e.preventDefault();
        onDone();
      }}
      // 🚨 Chrome 은 사용자 활성화 없이 받은 Esc 에는 `cancel` 없이 바로 닫는다 (닫기를 거듭 막지 못하게 한 규칙).
      //    그대로 두면 덮개만 사라지고 투어는 떠 있는 것으로 남아 스크롤 잠금이 풀리지 않는다 — 닫힘도 그만 보기로 센다.
      // 🚨 **다시 열려 있으면 무시한다.** 개발 모드(StrictMode)는 효과를 한 번 정리했다 다시 돌려서, 정리의
      //    `close()` 가 낸 `close` 이벤트가 다시 연 뒤에 도착한다 — 그걸 세면 뜨자마자 "봤음" 으로 닫힌다 (e2e 에서 그랬다).
      onClose={() => {
        if (closingRef.current || dialogRef.current?.open) return;
        onDone();
      }}
      className="fixed inset-0 m-0 h-full max-h-none w-full max-w-none overflow-hidden bg-transparent p-0 backdrop:bg-transparent"
    >
      {/* 구멍. 바깥으로 아주 넓게 퍼진 그림자가 곧 덮개다 — 덮개를 네 조각으로 자르지 않아도 된다.
          비출 것을 아직 못 쟀으면 덮개만 깐다. */}
      <div
        aria-hidden
        className={cn(
          "pointer-events-none fixed transition-opacity duration-(--motion-swap)",
          "shadow-[0_0_0_2px_var(--color-surface),0_0_0_200vmax_var(--color-scrim)]",
          opened ? "opacity-100" : "opacity-0",
        )}
        style={
          rect
            ? {
                top: rect.top,
                left: rect.left,
                width: rect.width,
                height: rect.height,
                borderRadius: rect.radius,
              }
            : { top: "50%", left: "50%", width: 0, height: 0 }
        }
      />

      <div
        ref={tipRef}
        className={cn(
          "max-w-content fixed inset-x-0 mx-auto px-4",
          "transition-opacity duration-(--motion-swap)",
          shown && tipTop !== null ? "opacity-100" : "opacity-0",
        )}
        style={{ top: tipTop ?? 0 }}
      >
        {/* 🚨 그림자를 주지 않는다 — "떠 있음" 은 바텀시트 하나뿐이다 (디자인 시스템 §6). 덮개 위라 테두리로 충분히 선다. */}
        <div className="bg-surface rounded-card border-line border p-4">
          <p className="text-caption text-ink-muted tabular-nums">
            {/* 화면 읽개는 "6단계 중 2단계" 로 읽는다. 눈에는 짧은 쪽을 둔다. */}
            <span aria-hidden>
              {view + 1} / {steps.length}
            </span>
            <span className="sr-only">
              {steps.length}단계 중 {view + 1}단계
            </span>
          </p>
          <h2
            id={titleId}
            ref={headingRef}
            tabIndex={-1}
            className="text-section text-ink mt-1 focus:outline-none"
          >
            {step.title}
          </h2>
          <p id={bodyId} className="text-body-sm text-ink-muted mt-1">
            {step.body}
          </p>
          <div className="mt-4 flex items-center gap-2">
            {/* 🚨 "건너뛰기" 라고 쓰지 않는다 — 이 단계만 넘기는 것으로 읽혔다(사용자 피드백). 남은 설명 전체를
                끝낸다는 것을 말한다. 마지막 단계에는 없다 — 끝낼 남은 것이 없고 "다 봤어요" 가 같은 일을 한다. */}
            {!last ? (
              <Button variant="tertiary" size="compact" className="mr-auto" onClick={onDone}>
                그만 볼래요
              </Button>
            ) : (
              <span className="mr-auto" />
            )}
            {view > 0 ? (
              <Button variant="secondary" size="compact" onClick={() => goTo(index - 1)}>
                이전
              </Button>
            ) : null}
            <Button size="compact" onClick={() => (last ? onDone() : goTo(index + 1))}>
              {last ? "다 봤어요" : "다음"}
            </Button>
          </div>
        </div>
      </div>
    </dialog>
  );
}
