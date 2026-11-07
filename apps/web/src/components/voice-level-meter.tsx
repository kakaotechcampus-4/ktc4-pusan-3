"use client";

import { type CSSProperties, useEffect, useState } from "react";

import { subscribeSpeech } from "@/lib/native/speech";

/** 막대 다섯 개의 키. 가운데가 가장 높은 모양이라 소리가 없어도 "듣는 자리" 로 읽힌다. */
const BARS = [0.45, 0.7, 1, 0.7, 0.45];
/** 소리가 없을 때 막대가 줄어드는 바닥. 0 이면 막대가 사라져 꺼진 것처럼 보인다. */
const FLOOR = 0.25;

/**
 * 말로 적기의 음량 막대 (#595 · 디자인 시스템 §8 "듣는 동안의 음량 막대").
 *
 * 마이크가 지금 소리를 듣고 있는지를 보여준다 — 지우면 "눌렀는데 듣고 있나?" 를 잃는다.
 *
 * 🚨 **애니메이션이 아니라 값을 그린다.** 셸이 0.1초마다 주는 크기로 막대 높이만 바꾸고 전환(`transition`)을
 *    붙이지 않는다. 🚨 **동작 줄이기에서는 멈춘 모양으로 선다**(`motion-reduce:scale-y-100`). 듣는 중이라는 것은
 *    위 안내 줄과 눌린 버튼이 말한다.
 * 🚨 **채팅바가 아니라 이 컴포넌트가 이벤트를 듣는다.** 0.1초마다 채팅바 전체(입력창 포함)를 다시 그리지 않게.
 * 장식이라 `aria-hidden` 이다.
 */
export function VoiceLevelMeter() {
  const [level, setLevel] = useState(0);

  useEffect(
    () =>
      subscribeSpeech((event) => {
        if (event.type === "volume") setLevel(event.level);
        else if (event.type === "start" || event.type === "end") setLevel(0);
      }),
    [],
  );

  return (
    <span aria-hidden className="text-ink-muted h-touch flex shrink-0 items-center gap-0.5 px-1">
      {BARS.map((height, i) => (
        <span
          key={i}
          className="block w-1 origin-center scale-y-(--bar) rounded-full bg-current motion-reduce:scale-y-100"
          // 높이는 막대 모양(고정)이고, 소리는 `--bar` 로 세로 배율만 바꾼다. 글자가 없는 장식이라 고정 높이를 쓴다.
          style={
            { height: `${height * 20}px`, "--bar": FLOOR + (1 - FLOOR) * level } as CSSProperties
          }
        />
      ))}
    </span>
  );
}
