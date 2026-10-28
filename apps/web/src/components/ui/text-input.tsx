import type { InputHTMLAttributes, ReactNode, Ref } from "react";
import { useId } from "react";

import { cn } from "@/lib/cn";

/**
 * 디자인 시스템 §7 입력. 라벨 · 설명 · 사유를 입력과 한 덩어리로 묶는다.
 *
 * ⚠️ 글자 크기를 16px 미만으로 내리지 않는다 (text-body).
 *    iOS 웹뷰는 16px 미만 입력에 포커스가 가면 화면을 자동 확대하고 그대로 남는다 (문서 §4).
 *
 * ⚠️ **`leading` · `trailing` 은 검색칸(07)을 위해 열어 둔 자리다** (#584). 앞은 무엇을 하는 칸인지
 *    말하는 장식 아이콘, 뒤는 지우기 같은 아이콘 버튼 하나다. 입력의 좌우 여백이 그만큼 넓어진다.
 * 🚨 `type="search"` 의 **브라우저 지우기 버튼은 숨긴다.** WebKit 이 칸 안에 그려 넣는 ×는 OS 마다
 *    생김새가 달라 토큰으로 못 맞춘다 (`<input type="date">` 를 안 쓰는 것과 같은 이유) — 지우기가
 *    필요하면 `trailing` 에 아이콘 버튼을 둔다.
 */
interface TextInputProps extends Omit<InputHTMLAttributes<HTMLInputElement>, "id"> {
  label: string;
  /**
   * 라벨을 `sr-only` 로 옮긴다. 🚨 지우는 것이 아니다 — 보조기술은 그대로 읽는다.
   * 칸이 하는 일이 `leading` 아이콘과 placeholder 로 보이는 검색칸에서만 쓴다 (`Select` 의 `labelHidden` 과 같은 조건).
   */
  labelHidden?: boolean;
  /** 라벨 아래 설명. 왜 묻는지를 여기에 쓴다. */
  hint?: ReactNode;
  /** 채우면 aria-invalid 가 붙고 입력 아래에 사유가 붙는다. */
  error?: string | null;
  /** 입력 앞의 장식 아이콘. 🚨 `aria-hidden` 이어야 한다 — 이름은 라벨이 진다. */
  leading?: ReactNode;
  /** 입력 뒤의 아이콘 버튼 하나 (지우기). */
  trailing?: ReactNode;
  /** 지우기 뒤에 포커스를 칸으로 돌려줄 때 쓴다 (React 19 — `ref` 가 그냥 prop 이다). */
  ref?: Ref<HTMLInputElement>;
}

export function TextInput({
  label,
  labelHidden = false,
  hint,
  error,
  leading,
  trailing,
  className,
  ...props
}: TextInputProps) {
  const id = useId();
  const hintId = `${id}-hint`;
  const errorId = `${id}-error`;

  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={id} className={labelHidden ? "sr-only" : "text-label text-ink-muted"}>
        {label}
      </label>
      {hint ? (
        <p id={hintId} className="text-caption text-ink-subtle">
          {hint}
        </p>
      ) : null}
      <div className="relative flex items-center">
        {leading ? (
          <span className="text-ink-subtle pointer-events-none absolute left-3.5 flex">
            {leading}
          </span>
        ) : null}
        <input
          id={id}
          aria-invalid={error ? true : undefined}
          aria-describedby={cn(hint ? hintId : null, error ? errorId : null) || undefined}
          className={cn(
            // 높이는 최소값이다 — 글자를 키우면 입력도 따라 커져야 값이 잘리지 않는다 (문서 §10).
            "text-body text-ink placeholder:text-ink-subtle rounded-field bg-surface min-h-field w-full border py-2",
            "ease-standard transition-colors duration-120",
            "[&::-webkit-search-cancel-button]:appearance-none",
            // 🚨 `cn()` 은 tailwind-merge 가 아니다 — 좌우 여백은 경우마다 하나만 고른다.
            leading ? "pl-11" : "pl-3.5",
            trailing ? "pr-12" : "pr-3.5",
            error ? "border-danger" : "border-line-strong hover:border-ink-subtle",
            className,
          )}
          {...props}
        />
        {trailing ? <span className="absolute right-1 flex">{trailing}</span> : null}
      </div>
      {error ? (
        <p id={errorId} className="text-caption text-danger-ink">
          {error}
        </p>
      ) : null}
    </div>
  );
}
