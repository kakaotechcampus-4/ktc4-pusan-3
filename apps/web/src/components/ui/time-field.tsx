"use client";

import { useState } from "react";

import { Select } from "./select";

/**
 * 시각 고르기 — **고르기 상자 둘로 만든 한 칸.**
 *
 * 🚨 **라이브러리를 얹지 않았다.** 디자인 시스템 §7 이 `react-day-picker` 를 얹은 이유는
 *    넷이다 — 그리드 ARIA · 방향키 이동 · 월 경계 처리 · 로케일. 시각 칸에는 **그 중 하나도
 *    해당되지 않고**, 12/24 변환과 "오전/오후" 는 `Intl` 이 이미 준다. 반대로 후보로 본
 *    `react-time-picker` 는 오전/오후를 **네이티브 `<select>`** 로, 모바일 대체 입력을
 *    `<input type="time">` 으로 그린다 — 둘 다 이 시스템이 이미 안 쓰기로 한 것이다
 *    (§7 "고르기 상자" · `<input type="date">` 를 막은 것과 같은 이유 · #151).
 *
 * 🚨 **접근성은 `Select` 가 이미 진다.** combobox + listbox ARIA · 포커스 복귀 · ESC · 바깥
 *    클릭 · 방향키가 그 파일에 있고, 여기서 다시 짜지 않는다. 새로 얹는 접근성 부담이 0 인 것이
 *    직접 만들기로 한 이유의 절반이다.
 *
 * 🚨 **상자는 둘이다 (오전/오후를 따로 두지 않는다).** 시 목록이 24개이고 라벨이 "오전 9시"
 *    라서 상자 하나가 이미 오전/오후를 진다. 셋으로 나누면 좁은 폰에서 칸이 셋으로 쪼개지고,
 *    **덜 고른 상태가 하나 더** 생긴다(오전만 고르고 시를 안 고른 상태).
 *
 * 🚨 **덜 고른 것은 고른 것이 아니다.** 시만 고르고 분을 안 골랐으면 `onChange("")` 다 —
 *    "9시면 당연히 9:00" 은 화면이 대신 정하는 값이고, 이 칸이 있는 이유가 그걸 막는 것이다
 *    (`lib/event-draft.ts` 머리말). 대신 **무엇이 남았는지 글자로** 말한다.
 *
 * 🚨 **묶음 라벨이 상자의 문구와 겹치지 않아야 한다.** 라벨을 "몇 시" 로 뒀더니 바로 아래
 *    상자에도 "몇 시" 가 떠서 같은 말이 두 번 섰다 — 라벨은 **무엇을 정하는 칸인가**("시작 시간"),
 *    상자의 문구는 **그 칸에서 아직 안 고른 것**("몇 시" · "몇 분")이라 서로 다른 일을 한다.
 *
 * 🚨 **라벨은 묶음이 진다.** 상자마다 "시" · "분" 을 또 세우면 한 칸에 라벨이 셋이다. 상자의
 *    라벨은 `labelHidden` 으로 보조기술에만 남기고(지우는 것이 아니다), 눈에 보이는 이름은
 *    이 묶음의 것 하나다 — 값 자체가 단위를 지고 있어서 눈으로는 읽을 것이 남는다.
 *
 * ⚠️ **바깥에서 `value` 를 비워도 두 상자는 그대로다.** "고르는 중" 은 이 칸이 자기 안에
 *    들고 있어서, 바깥이 값을 되돌리는 경로가 생기면 `key` 로 다시 마운트해야 한다.
 *    지금 쓰는 두 곳(초안 카드 · 사진 고치기 시트)에는 그런 경로가 없다.
 */

/** 🚨 5분 단위다. 1분 단위는 60줄을 훑게 하고, 10분 단위는 10:20 같은 병원 예약을 못 담는다. */
const MINUTE_STEP = 5;

const HOUR_OPTIONS = Array.from({ length: 24 }, (_, hour) => ({
  value: pad(hour),
  label: `${hour < 12 ? "오전" : "오후"} ${hour % 12 === 0 ? 12 : hour % 12}시`,
}));

export function TimeField({
  label,
  hint,
  value,
  onChange,
}: {
  label: string;
  hint?: string;
  /** `HH:MM`(24시간) 또는 빈 문자열. 🚨 빈 문자열은 **아직 안 골랐다**는 뜻이다. */
  value: string;
  onChange: (value: string) => void;
}) {
  const [hour, setHour] = useState(() => (value === "" ? "" : value.slice(0, 2)));
  const [minute, setMinute] = useState(() => (value === "" ? "" : value.slice(3, 5)));

  function commit(nextHour: string, nextMinute: string) {
    setHour(nextHour);
    setMinute(nextMinute);
    onChange(nextHour !== "" && nextMinute !== "" ? `${nextHour}:${nextMinute}` : "");
  }

  /**
   * 🚨 **눈금 밖의 값도 목록에 선다.** 알림장에서 10:23 을 읽어 왔는데 5분 단위 목록에 없으면,
   *    화면이 그 값을 못 보여주거나(빈 칸) 가까운 눈금으로 **반올림**하게 된다 — 둘 다
   *    서버가 준 값을 화면이 바꾸는 것이다.
   */
  const minuteOptions = withOffGrid(minute);

  const partial =
    hour !== "" && minute === ""
      ? "몇 분인지도 골라주세요."
      : hour === "" && minute !== ""
        ? "몇 시인지도 골라주세요."
        : null;
  const shown = partial ?? hint;

  return (
    // 🚨 상자 둘이 한 값이라 묶음으로 읽혀야 한다 — 이름은 여기 하나뿐이다 (위 머리말).
    <div role="group" aria-label={label} className="flex flex-col gap-1.5">
      <span className="text-label text-ink-muted">{label}</span>
      {shown ? <p className="text-caption text-ink-subtle">{shown}</p> : null}

      <div className="flex items-start gap-2">
        <Select
          label={`${label} — 시`}
          labelHidden
          shape="field"
          placeholder="몇 시"
          value={hour}
          options={HOUR_OPTIONS}
          onChange={(next) => commit(next, minute)}
        />
        <Select
          label={`${label} — 분`}
          labelHidden
          shape="field"
          placeholder="몇 분"
          value={minute}
          options={minuteOptions}
          onChange={(next) => commit(hour, next)}
        />
      </div>
    </div>
  );
}

function pad(value: number): string {
  return String(value).padStart(2, "0");
}

function withOffGrid(minute: string): ReadonlyArray<{ value: string; label: string }> {
  const steps = Array.from({ length: 60 / MINUTE_STEP }, (_, i) => i * MINUTE_STEP);
  const current = Number(minute);

  if (minute !== "" && Number.isInteger(current) && !steps.includes(current)) {
    steps.push(current);
    steps.sort((a, b) => a - b);
  }

  return steps.map((value) => ({ value: pad(value), label: `${pad(value)}분` }));
}
