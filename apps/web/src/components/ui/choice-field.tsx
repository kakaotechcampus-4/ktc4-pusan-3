"use client";

import { Check } from "lucide-react";
import { useId } from "react";

import { ICON_SIZE, ICON_STROKE } from "@/components/ui/icon";
import { cn } from "@/lib/cn";

/**
 * 디자인 시스템 §7 `choice-field` — **반드시 하나**를 고르는 칸. 둘~셋은 가로로 나누고,
 * 넷 이상이거나 문장이 길면 세로로 쌓는다 (아래 "세로").
 *
 * ## 🚨 왜 `Select` 가 아닌가
 *
 * 드롭다운은 **고를 것이 많거나 화면에 다 못 세울 때** 쓴다. 두 가지뿐이면 그 상자는
 * 있는 선택지를 한 번 **감췄다가** 탭 두 번으로 다시 보여주는 일만 한다 — 화면에 그냥
 * 써 두면 될 것을 상호작용으로 바꾸는 셈이다. 선택지가 둘이면 **둘 다 보이는 것**이 맞다.
 *
 * ## 🚨 왜 `chip-choice` 도 아닌가
 *
 * 칩은 `aria-pressed` 짜리 토글 여러 개다 — 01 관심사처럼 **껐다 켰다 하는** 자리의 물건이고,
 * 보조기술에는 "둘 중 하나" 라는 관계가 드러나지 않으며 **둘 다 꺼진 상태**가 정상으로 읽힌다.
 * 이 칸은 반대로 **비어 있을 수 없는 값**이라, 그 관계를 구조가 말해야 한다.
 *
 * ## 🚨 그래서 네이티브 라디오다
 *
 * `<input type="radio">` 를 `sr-only` 로 숨기고 표식만 그린다 (`Checkbox` 와 같은 처리).
 * 같은 `name` 을 주면 **그룹 · 방향키 이동 · 단 하나만 선택 · 폼 의미**를 브라우저가 그대로
 * 준다 — 직접 만든 `role="radiogroup"` 보다 언제나 정확하다. 이 저장소에서 손으로 만든 것은
 * `Select` 하나뿐이고, 그건 네이티브로 **생김새를 못 정해서** 진 빚이다 (`select.tsx` 머리말).
 * 여기는 그 사정이 없다.
 *
 * 🚨 **고른 것을 색 하나로 말하지 않는다** — 체크 아이콘이 같이 선다 (§3 · `Select` 의 항목과
 *    같은 언어다). `brand-soft` 배경 위 글자는 `brand-ink` 다. `brand` 가 아니다 (§2 · §5).
 * 🚨 **`min-h-field` 다.** 같은 시트의 `TextInput`·`DateField` 와 한 줄 높이를 맞춘다 —
 *    폼 안에서 한 칸만 낮으면 그 칸이 입력이 아니라 장식으로 읽힌다.
 * 🚨 **`active:` 를 빠뜨리지 않는다.** 웹뷰에는 호버가 없어서 `hover:` 만 두면 눌러도 아무
 *    반응이 없다 (apps/web/CLAUDE.md §5).
 *
 * ## 세로 (`direction="vertical"`)
 *
 * 선택지가 넷 이상이거나 문장이 길어서 가로로 똑같이 나눌 수 없을 때 쓴다 (10 설정 › 문제
 * 알리기 · #487). 상자 · 색 · 상태는 가로와 **같은 표**를 쓴다 — 화면에서 따로 그리면 이 칸의
 * 색이 바뀔 때 그 화면만 남는다. 다른 것은 놓는 방식뿐이다: 칸을 한 줄에 하나씩 쌓고, 글자는
 * 왼쪽에 붙이고, 체크 아이콘은 글자 앞에 흐름 안에서 선다.
 * 🚨 **세로여도 비어 있을 수 없다.** 선택지가 많다고 빈 값을 허용하지 않는다 — 기본값을 주고,
 *    그럴 수 없는 칸이면 이 컴포넌트가 아니다.
 */
export function ChoiceField<T extends string>({
  label,
  value,
  options,
  onChange,
  hint,
  direction = "horizontal",
  disabled = false,
}: {
  label: string;
  /** 🚨 **비어 있을 수 없다.** 그 사정이 있는 칸이면 이 컴포넌트가 아니라 `Select` 다. */
  value: T;
  options: ReadonlyArray<{ value: T; label: string }>;
  onChange: (value: T) => void;
  hint?: string;
  /** 넷 이상이거나 문장이 길면 `vertical` — 칸을 한 줄에 하나씩 쌓는다. */
  direction?: "horizontal" | "vertical";
  /**
   * 보내는 동안 고른 것을 못 바꾸게 묶음째 잠근다 (`<fieldset disabled>`).
   * ⚠️ 모양은 바꾸지 않는다 — 커서 · 호버가 그대로라 짧게 잠그는 자리(보내는 동안)에만 쓴다.
   */
  disabled?: boolean;
}) {
  const vertical = direction === "vertical";
  const id = useId();
  const hintId = hint ? `${id}-hint` : undefined;

  return (
    /* 🚨 `<fieldset>` + `<legend>` 이라 그룹 이름이 각 선택지에 저절로 붙는다 —
       스크린리더가 "성별, 남자아이, 라디오 버튼, 2 중 1" 로 읽는다. */
    <fieldset className="flex min-w-0 flex-col gap-1" disabled={disabled}>
      <legend className="text-caption text-ink-subtle">{label}</legend>
      {hint ? (
        <p id={hintId} className="text-caption text-ink-subtle">
          {hint}
        </p>
      ) : null}

      {/* 🚨 칸을 똑같이 나눈다(`flex-1` + `basis-0`). 글자 길이에 따라 칸 너비가 달라지면
          한쪽이 기본값처럼 보인다 — 여기서는 어느 쪽도 기본이 아니다. 세로는 칸이 모두 한 줄을
          꽉 채워서 이 문제가 없다. */}
      <div className={cn("mt-1 flex gap-2", vertical && "flex-col")}>
        {options.map((option) => {
          const selected = option.value === value;

          return (
            <label
              key={option.value}
              className={cn(
                "rounded-field min-h-field ease-standard flex cursor-pointer items-center border py-2 transition-colors duration-120",
                vertical
                  ? // 세로는 글자를 왼쪽에 붙인다 — 체크 아이콘이 흐름 안에서 글자 앞에 선다.
                    "gap-2 px-3.5"
                  : // 🚨 `relative` 는 체크 아이콘의 기준이다 (아래 주석).
                    // 🚨 좌우 여백이 `px-6`(24px) 인 이유는 체크 아이콘이다 — 아래 주석.
                    "group relative flex-1 basis-0 justify-center px-6 text-center",
                // 🚨 포커스 링은 숨은 `<input>` 이 아니라 이 상자에 그린다 (`Checkbox` 와 같다).
                "has-focus-visible:outline-brand has-focus-visible:outline-2 has-focus-visible:outline-offset-2",
                selected
                  ? "bg-brand-soft border-brand text-brand-ink"
                  : "bg-surface border-line-strong text-ink-muted hover:bg-surface-muted hover:border-ink-subtle active:bg-surface-muted active:border-ink-subtle",
              )}
            >
              <input
                type="radio"
                name={id}
                value={option.value}
                checked={selected}
                aria-describedby={hintId}
                onChange={() => onChange(option.value)}
                className="sr-only"
              />
              {/*
                🚨 **늘 그려 둔다** — 고를 때 아이콘이 생기면서 글자가 밀리면 "색만
                   바뀐다"(§8)가 깨지고 칸이 들썩인다. 안 고른 상태는 `text-transparent` 다.
                🚨 **흐름에서 빼서 왼쪽에 띄운다.** 예전에는 글자 옆에 나란히 섰는데, 그러면
                   아이콘 + 간격(22px)이 가운데 정렬의 기준을 옮겨서 **글자가 상자 중앙에서
                   11px 오른쪽으로 밀렸다.** 칸마다 글자 길이가 달라 그 어긋남이 눈에 띄었다.
                   `absolute` 면 자리를 차지하지 않으므로 글자는 상자 정중앙에 서고,
                   고를 때 움직이는 것은 여전히 색뿐이다.
                🚨 **그래서 좌우 여백이 `px-6`(24px) 다.** 아이콘은 `left-1.5`(6px)에서 22px
                   까지를 쓰므로 글자가 24px 부터 시작하면 닿지 않는다. **여백을 좌우
                   같게 두는 것이 핵심**이다 — 왼쪽만 키우면 글자가 다시 오른쪽으로 밀린다.
                   🚨 `px-3` 으로 줄이지 말 것. 그러면 390px 폭에서 긴 라벨이 한 줄에
                   들어가면서 **아이콘 위로 올라탄다** (실제로 그렇게 겹쳤다).
                세로는 글자가 왼쪽에 붙어서 가운데 정렬이 어긋날 일이 없다 — 흐름 안에 둔다.
                늘 그려 두는 것은 같다(안 고른 칸의 글자도 같은 자리에서 시작한다).
              */}
              <Check
                aria-hidden
                size={ICON_SIZE.sm}
                strokeWidth={ICON_STROKE}
                className={cn(
                  vertical
                    ? "shrink-0"
                    : "pointer-events-none absolute top-1/2 left-1.5 -translate-y-1/2",
                  selected ? null : "text-transparent",
                )}
              />
              <span className="text-body-sm">{option.label}</span>
            </label>
          );
        })}
      </div>
    </fieldset>
  );
}
