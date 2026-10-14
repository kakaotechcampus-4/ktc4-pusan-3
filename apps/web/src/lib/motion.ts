"use client";

import { useCallback, useEffect, useLayoutEffect, useRef } from "react";

/**
 * 화면 **안**의 움직임 — 교체 · 단계 넘김 · 자리 옮김 (디자인 시스템 §8).
 *
 * 화면 전환은 View Transitions(`components/route-transition.tsx`), 바텀시트는 CSS(`.bottom-sheet`)가
 * 맡는다. 여기 있는 것은 **같은 화면에서 상태가 바뀌는 순간**이라 Web Animations API 로 그린다 —
 * View Transitions 는 `setState` 로 안 켜지고(Next 문서 `view-transitions.md`), 켜지더라도 화면 전체를
 * 찍어서 그 동안 다른 움직임을 막는다.
 *
 * 🚨 **처음 그려질 때는 움직이지 않는다. 바뀔 때만 움직인다.** 화면이 뜰 때마다 무언가 올라오면 그건
 *    §8 이 막는 등장 애니메이션이고, 100번째에는 매번 기다려야 하는 것이 된다. 아래 훅은 전부 첫 렌더를
 *    건너뛴다 — 캐시가 있어 스켈레톤 없이 바로 선 화면도 움직이지 않는다.
 * 🚨 **움직이는 속성은 `translate` · `opacity` 뿐이다.** `height` · `scale` · `filter` 를 쓰지 않는다
 *    (높이는 레이아웃을 다시 계산하고, scale 은 손글씨를 번지게 한다 · §8).
 * 🚨 **시간 · 곡선 · 거리는 `globals.css` 의 토큰을 읽는다.** 숫자를 여기 적지 않는다 — 한동안 규칙마다
 *    숫자가 흩어져 표에 없는 값이 생겼다 (#231 리뷰). 토큰을 못 읽으면(테스트 환경) 움직이지 않는다.
 * 🚨 **reduced-motion 에서는 위치 이동을 끄고 투명도만 남긴다** (§8). `globals.css` 의 `*` 규칙은
 *    CSS 애니메이션만 끄고 Web Animations 에는 닿지 않아서, 여기서 직접 본다.
 */

type Duration = "fast" | "base" | "sheet" | "screen";
type Easing = "standard" | "enter" | "exit";

function readToken(name: string): string {
  if (typeof window === "undefined") return "";
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

function duration(name: Duration): number {
  const raw = readToken(`--motion-${name}`);
  const value = Number.parseFloat(raw);
  if (!Number.isFinite(value)) return 0;
  return raw.endsWith("ms") ? value : raw.endsWith("s") ? value * 1000 : value;
}

function easing(name: Easing): string {
  return readToken(`--ease-${name}`) || "linear";
}

function stepDistance(): number {
  const value = Number.parseFloat(readToken("--motion-distance-step"));
  return Number.isFinite(value) ? value : 0;
}

export function prefersReducedMotion(): boolean {
  return (
    typeof window !== "undefined" &&
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches
  );
}

function canAnimate(el: Element | null): el is HTMLElement {
  return el instanceof HTMLElement && typeof el.animate === "function";
}

/**
 * 같은 자리에 새 내용이 들어설 때의 움직임.
 * - `fade` — 교체. 스켈레톤 → 내용, 진행 표시 → 결과, 버튼 묶음 → 확인 패널, 펼친 내용
 * - `forward` · `back` — 단계 넘김. 한 칸 앞(뒤)으로 갔다는 것을 24px 가로 이동이 말한다
 *   (시트 1 → 2단계, 달 넘기기, 초대 코드 → 아이 확인)
 */
export type EnterMotion = "fade" | "forward" | "back";

/** 그 요소를 지금 막 들어선 것처럼 움직인다. 첫 렌더 판단은 호출하는 쪽 몫이다 (`useEnterMotion`). */
export function playEnter(el: Element | null, motion: EnterMotion = "fade") {
  if (!canAnimate(el)) return;
  const fade = duration("base");
  if (fade === 0) return;

  const reduce = prefersReducedMotion();
  if (motion === "fade" || reduce) {
    el.animate([{ opacity: 0 }, { opacity: 1 }], { duration: fade, easing: easing("standard") });
    return;
  }

  // 들어오는 쪽만 움직인다 — 나가는 내용은 이미 DOM 에 없다. 떠나는 화면이 비켜나는 거리(24px)만큼만
  // 밀려 들어와서, 화면 전환(72px)보다 작은 일이라는 것이 거리로 읽힌다.
  const offset = (motion === "forward" ? 1 : -1) * stepDistance();
  el.animate([{ opacity: 0 }, { opacity: 1 }], { duration: fade, easing: easing("enter") });
  el.animate([{ translate: `${offset}px 0` }, { translate: "0 0" }], {
    duration: duration("sheet"),
    easing: easing("enter"),
  });
}

type MotionKey = string | number | boolean | null | undefined;

/**
 * `key` 가 바뀔 때마다 ref 가 가리키는 요소에 `playEnter` 를 건다. **처음 그려질 때는 안 건다.**
 * 데이터가 바꾸는 자리(스켈레톤 → 내용 · 달 넘기기)에 쓴다. 사용자가 누른 순간에만 움직여야 하면
 * `useMotionCue` 다.
 *
 * ```tsx
 * const ref = useEnterMotion(phase);
 * return <div ref={ref}>{phase === "pending" ? <Skeleton /> : <Body />}</div>;
 * ```
 *
 * 🚨 `key` 는 원시값이어야 한다 (매 렌더 새 객체면 매번 움직인다). ref 는 기본이 `div` 다 — 다른 요소면
 *    두 번째 타입 인자로 준다.
 * 🚨 `motion` 에 함수를 주면 예전 key 와 새 key 로 방향을 고른다 (`null` 이면 그 변화는 움직이지 않는다).
 * 🚨 `target` 은 ref 안에서 실제로 움직일 요소를 고른다 — 달력처럼 머리줄(누르고 있는 화살표)은
 *    두고 날짜 칸만 움직여야 할 때. 누르는 손가락 밑에서 버튼이 미끄러지면 연달아 누르기가 어긋난다.
 * 🚨 레이아웃 효과에서 건다 — 일반 효과면 새 내용이 한 프레임 다 보였다가 투명으로 돌아가 깜빡인다.
 */
export function useEnterMotion<K extends MotionKey, T extends HTMLElement = HTMLDivElement>(
  key: K,
  motion: EnterMotion | ((from: K, to: K) => EnterMotion | null) = "fade",
  target?: (root: T) => Element | Iterable<Element> | null,
) {
  const ref = useRef<T>(null);
  const last = useRef(key);

  useLayoutEffect(() => {
    const from = last.current;
    if (Object.is(from, key)) return;
    last.current = key;
    const root = ref.current;
    if (!root) return;
    const chosen = typeof motion === "function" ? motion(from, key) : motion;
    if (!chosen) return;
    const picked = target ? target(root) : root;
    if (picked instanceof Element || picked === null) playEnter(picked, chosen);
    else for (const el of picked) playEnter(el, chosen);
    // motion · target 은 key 가 바뀐 그 순간의 값만 쓴다 — 그 둘만 바뀐 렌더에서는 움직이지 않는다.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  return ref;
}

/**
 * 펼침 — `show` 가 참이 될 때 그 내용이 투명도로 선다 (디자인 시스템 §8 "펼침"). 접을 때는 즉시다.
 * ref 는 `show` 일 때만 그려지는 요소에 단다. **높이는 움직이지 않는다** — 자라는 동안 아래가 끌려
 * 내려가며 흔들린다. 아래 줄들이 비켜나는 것은 그 목록의 `useFlip` 이 맡는다.
 */
export function useReveal<T extends HTMLElement = HTMLDivElement>(show: boolean) {
  return useEnterMotion<boolean, T>(show, (_, to) => (to ? "fade" : null));
}

/**
 * **사용자가 누른 순간**에만 움직이는 교체 · 단계 넘김. 이벤트 처리기에서 `cue("forward")` 를 부르고
 * 상태를 바꾸면, 그 다음 그려진 내용에 움직임이 걸린다.
 *
 * `useEnterMotion` 과 나눈 이유 — 같은 상태가 사용자 말고도 바뀌는 자리가 있다. 08 사진 고르기 시트는
 * 다시 열 때 1단계로 **되감는데**, key 로 보면 그것도 "2 → 1단계" 라서 열리는 시트 위에서 단계가 한 번
 * 더 미끄러진다. 누른 것만 움직이게 하면 그 구분을 따로 할 필요가 없다.
 *
 * 🚨 `cue` 를 부른 컴포넌트가 다시 그려져야 움직인다 — 상태가 그 컴포넌트(또는 그 위)에 있어야 한다.
 */
export function useMotionCue<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  const pending = useRef<EnterMotion | null>(null);

  // 의존성 없이 매 렌더 뒤에 본다 — 신호가 없으면 아무것도 안 한다.
  useLayoutEffect(() => {
    const motion = pending.current;
    if (!motion) return;
    pending.current = null;
    playEnter(ref.current, motion);
  });

  const cue = useCallback((motion: EnterMotion) => {
    pending.current = motion;
  }, []);

  return { ref, cue };
}

/** 한 항목의 자리. `layout` 은 스크롤을 되돌린 자리(옮겼는지 판단), `seen` 은 화면에 보이던 자리(출발점). */
type FlipSpot = { layout: { x: number; y: number }; seen: { x: number; y: number } };

/** 위치를 비교할 때 무시하는 오차(px). 서브픽셀 반올림으로 1px 안쪽은 늘 흔들린다. */
const FLIP_EPSILON = 1;

/** 지금 그려진 위치에서 레이아웃 위치를 뺀 값 — 도는 움직임이 있으면 그만큼 밀려 있다. */
function currentShift(el: HTMLElement): { x: number; y: number } {
  const value = getComputedStyle(el).translate;
  if (!value || value === "none") return { x: 0, y: 0 };
  const [x = "0", y = "0"] = value.split(" ");
  return { x: Number.parseFloat(x) || 0, y: Number.parseFloat(y) || 0 };
}

/**
 * 그 요소와 `root` 사이(둘 다 포함)의 스크롤 양. 위치에서 이걸 되돌려 두어야 **안쪽 목록을 굴린 것**을
 * "자리를 옮겼다" 로 읽지 않는다 — 08 의 "잘 읽었어요" 는 스무 줄이 넘으면 상자 안에서 굴러간다.
 */
function scrolledBy(el: HTMLElement, root: HTMLElement): { x: number; y: number } {
  let x = 0;
  let y = 0;
  for (let node = el.parentElement; node; node = node.parentElement) {
    x += node.scrollLeft;
    y += node.scrollTop;
    if (node === root) break;
  }
  return { x, y };
}

/**
 * 자리 옮김 — 목록의 항목이 다른 그룹으로 옮겨 가거나, 하나가 빠지고 더해질 때 **나머지가 새 자리로
 * 미끄러진다** (FLIP · 디자인 시스템 §8). 지웠을 때 "방금 넣은 줄이 화면에서 사라진 것처럼 보이는"
 * 자리(08 사진 확인 · 11-2 검사지 확인)에서 시작했다.
 *
 * ref 를 목록들을 **함께** 감싸는 요소에 달고, 움직일 항목마다 `data-flip="{id}"` 를 단다. 두 그룹이
 * 같은 바깥 요소 안에 있으면 그룹을 건너가는 항목도 같은 id 로 이어진다.
 *
 * - 자리가 바뀐 항목 → 예전 자리에서 새 자리로 (`screen` · `--ease-enter`)
 * - 새로 생긴 항목 → 투명도로만 선다 (`base`) · 빠진 항목 → 즉시 사라지고 나머지가 그 자리를 메운다
 *
 * 🚨 **처음 그려질 때는 아무것도 움직이지 않는다** (비교할 예전 자리가 없다).
 * 🚨 **승인 게이트 화면은 `fadeIn: false` 로 쓴다** — 새로 나타나는 줄은 확인해야 할 것이라 투명도로
 *    흘려보내지 않는다 (§8 "하지 않는 것"). 이미 보고 있던 줄이 자리를 옮기는 것만 움직인다.
 * 🚨 위치는 감싸는 요소 기준으로 잰다 — 위쪽 내용이 늘어 목록 전체가 밀린 것은 "자리 옮김" 이 아니다.
 * 🚨 접혀 있는 그룹으로 옮겨 간 항목은 보이지 않으니 움직일 수 없다. 그 자리는 호출하는 쪽이 펼친다.
 */
export function useFlip<T extends HTMLElement>({ fadeIn = true }: { fadeIn?: boolean } = {}) {
  const ref = useRef<T>(null);
  const last = useRef<Map<string, FlipSpot> | null>(null);
  const watcher = useRef<ResizeObserver | null>(null);

  /**
   * 🚨 **줄 안쪽만 다시 그려져도 잰다.** 아래 레이아웃 효과는 감싸는 컴포넌트가 그려질 때만 도는데, 줄
   *    하나가 제 상태로 커지면(11-1 지우기 확인 패널) 그 아래 줄들이 밀려도 거기서는 모른다. 그대로 두면
   *    다음에 목록이 바뀔 때 낡은 자리와 비교해서, 확인 패널이 열렸던 만큼 한 번 더 미끄러진다 (실제로 그랬다).
   *    크기가 바뀐 것을 보고 같은 비교를 한다 — 밀려난 줄은 그 자리로 미끄러진다. ResizeObserver 는 레이아웃
   *    뒤 · 칠하기 전에 불려서 새 자리가 한 번 보였다가 돌아가는 깜빡임이 없다.
   */
  useEffect(() => {
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(() => {
      const root = ref.current;
      if (root && last.current) last.current = flipFrom(last.current, root, fadeIn);
    });
    watcher.current = observer;
    return () => {
      observer.disconnect();
      watcher.current = null;
    };
  }, [fadeIn]);

  // 의존성 없이 매 렌더 뒤에 잰다. 목록이 바뀐 렌더를 골라낼 방법이 "재 보는 것" 뿐이다.
  useLayoutEffect(() => {
    const root = ref.current;
    if (!root) return;
    last.current = last.current ? flipFrom(last.current, root, fadeIn) : measure(root).spots;

    // 지금 있는 줄들의 크기를 본다 (빠진 줄은 놓고, 새 줄은 더한다).
    const observer = watcher.current;
    if (observer) {
      observer.disconnect();
      observer.observe(root);
      for (const node of root.querySelectorAll("[data-flip]")) observer.observe(node);
    }
  });

  return ref;
}

/** 예전 자리(`before`)와 지금 자리를 비교해 움직임을 걸고, 지금 자리를 돌려준다. */
function flipFrom(before: Map<string, FlipSpot>, root: HTMLElement, fadeIn: boolean) {
  const { spots, nodes } = measure(root);
  const reduce = prefersReducedMotion();

  for (const { id, node, shift } of nodes) {
    const at = spots.get(id);
    if (!at || !canAnimate(node)) continue;
    const was = before.get(id);

    if (!was) {
      const ms = duration("base");
      if (fadeIn && ms > 0) {
        node.animate([{ opacity: 0 }, { opacity: 1 }], {
          duration: ms,
          easing: easing("standard"),
        });
      }
      continue;
    }

    const moved =
      Math.abs(was.layout.x - at.layout.x) >= FLIP_EPSILON ||
      Math.abs(was.layout.y - at.layout.y) >= FLIP_EPSILON;
    const ms = duration("screen");
    if (!moved || reduce || ms === 0) continue;

    // 출발점은 **보이던** 자리다 — 스크롤 상자를 건너간 줄은 상자 안 스크롤만큼 다른 데 있었다.
    // 움직이던 도중이면 그 움직임만큼 더해서 **지금 보이는 자리**에서 출발한다 — 처음 자리로 튀지 않게.
    const dx = was.seen.x - at.seen.x + shift.x;
    const dy = was.seen.y - at.seen.y + shift.y;
    for (const running of node.getAnimations()) {
      if (running.id === "flip") running.cancel();
    }
    const flight = node.animate([{ translate: `${dx}px ${dy}px` }, { translate: "0 0" }], {
      duration: ms,
      easing: easing("enter"),
    });
    flight.id = "flip";
  }

  return spots;
}

/**
 * `root` 안의 `[data-flip]` 자리를 잰다. 도는 움직임과 스크롤을 뺀 **레이아웃** 자리를 함께 적는다 —
 * 그래야 움직이는 도중에 다시 그려지거나 안쪽 목록을 굴린 뒤에 그려져도 같은 자리를 "옮겨 갔다" 로
 * 잘못 읽지 않는다.
 */
function measure(root: HTMLElement) {
  const origin = root.getBoundingClientRect();
  const spots = new Map<string, FlipSpot>();
  const nodes: Array<{ id: string; node: HTMLElement; shift: { x: number; y: number } }> = [];

  for (const node of root.querySelectorAll<HTMLElement>("[data-flip]")) {
    const id = node.dataset.flip;
    if (!id) continue;
    const rect = node.getBoundingClientRect();
    const shift = currentShift(node);
    const scroll = scrolledBy(node, root);
    const seen = { x: rect.left - origin.left - shift.x, y: rect.top - origin.top - shift.y };
    spots.set(id, { layout: { x: seen.x + scroll.x, y: seen.y + scroll.y }, seen });
    nodes.push({ id, node, shift });
  }

  return { spots, nodes };
}
