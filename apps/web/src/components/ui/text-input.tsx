import type { InputHTMLAttributes, ReactNode } from "react";
import { useId } from "react";

import { cn } from "@/lib/cn";

/**
 * 디자인 시스템 §7 입력. 라벨 · 설명 · 사유를 입력과 한 덩어리로 묶는다.
 *
 * ⚠️ 글자 크기를 16px 미만으로 내리지 않는다 (text-body).
 *    iOS 웹뷰는 16px 미만 입력에 포커스가 가면 화면을 자동 확대하고 그대로 남는다 (문서 §4).
 */
interface TextInputProps extends Omit<InputHTMLAttributes<HTMLInputElement>, "id"> {
  label: string;
  /** 라벨 아래 설명. 왜 묻는지를 여기에 쓴다. */
  hint?: ReactNode;
  /** 채우면 aria-invalid 가 붙고 입력 아래에 사유가 붙는다. */
  error?: string | null;
}

export function TextInput({ label, hint, error, className, ...props }: TextInputProps) {
  const id = useId();
  const hintId = `${id}-hint`;
  const errorId = `${id}-error`;

  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={id} className="text-label text-ink-muted">
        {label}
      </label>
      {hint ? (
        <p id={hintId} className="text-caption text-ink-subtle">
          {hint}
        </p>
      ) : null}
      <input
        id={id}
        aria-invalid={error ? true : undefined}
        aria-describedby={cn(hint ? hintId : null, error ? errorId : null) || undefined}
        className={cn(
          "text-body text-ink placeholder:text-ink-subtle rounded-field bg-surface h-13 w-full border px-3.5",
          error ? "border-danger" : "border-line-strong",
          className,
        )}
        {...props}
      />
      {error ? (
        <p id={errorId} className="text-caption text-danger-ink">
          {error}
        </p>
      ) : null}
    </div>
  );
}
