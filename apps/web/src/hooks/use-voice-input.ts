"use client";

import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";

import {
  SPEECH_NOTICE,
  type SpeechSupport,
  appendSpoken,
  getSpeechBridge,
  readSpeechSupport,
  subscribeSpeech,
} from "@/lib/native/speech";

/** 창구는 문서를 읽기 전에 꽂히고 바뀌지 않는다. 구독할 것이 없다. */
const noSubscribe = () => () => {};
/** 서버 렌더링과 hydrate 첫 그림은 셸 밖과 같다 — 둘이 어긋나면 hydrate 가 깨진다. */
const serverSupport = (): SpeechSupport => "none";

/**
 * 채팅바의 말로 적기 (#595 · `lib/native/speech.ts`).
 *
 * 들은 말은 **누를 때의 입력창 글 뒤에** 붙는다. 말하는 동안(`final` 이 아닐 때)은 같은 자리를 바꿔 끼우고,
 * 한 구간이 끝나면 그 뒤에 다음 구간을 붙인다 — Android 는 쉬었다 말하면 구간을 나눠 보낸다.
 * 끝내는 길은 둘이다 — 다 말했어요(`finish` · 들은 글을 남긴다), 들은 말 지우기(`discard` · 누를 때의 글로
 * 되돌린다). 듣는 동안은 보내기 버튼이 비키고 Enter 도 막혀 있어서, 보내는 것은 듣기가 끝난 뒤다.
 *
 * 🚨 **듣는 동안 입력창을 고칠 수 없게 한다**(`listening` 으로 `readOnly`). 붙일 자리를 누를 때의 글로 잡아
 *    두므로, 그 사이에 고친 글은 다음 결과가 덮는다.
 * 🚨 **듣는 동안 다른 코드가 입력창에 글을 넣으면 듣기를 버린다.** "고쳐 쓰기" 와 보내기 실패의 원문
 *    되돌리기가 그 길이다(`stores/conversation.ts` 의 `canHandBack` 은 입력창이 비었는지만 본다). 그대로 두면
 *    다음 결과가 누를 때의 글 뒤에 들은 말을 붙여 입력창을 통째로 바꾸고, 원문이 입력창에도 말풍선에도 남지
 *    않는다 (#231 "말풍선은 원문을 입력창에 옮겼을 때만 지운다").
 * 🚨 **버린 듣기의 글자는 입력창에 넣지 않는다**(`release`). 그만 듣기 뒤에도 마지막 구간이 한 번 더 올 수 있는데,
 *    되돌린 글이나 다른 코드가 넣은 글을 그것이 덮으면 안 된다.
 * 🚨 **버린 듣기의 `end` 가 오기 전에는 새로 듣지 않는다**(`drainingRef`). 셸 이벤트에는 어느 듣기의 것인지가
 *    없어서, 그 사이에 새로 시작하면 지난 듣기의 늦은 글자가 새 글 뒤에 붙고 늦은 `end` 가 새 듣기를 끈다.
 * 🚨 **이 채팅바가 시작하지 않은 듣기가 시작되면 멈춘다.** 셸은 듣는데 화면은 꺼져 있는 상태를 남기지 않는다.
 * 🚨 **문서가 내려가면(`pagehide`) 멈춘다.** 새로고침 · 하드 이동에서는 React 정리가 돌지 않는다. 셸도 새 문서를
 *    열 때 끊는다 (`apps/mobile/App.tsx` 의 `abortListening`).
 */

/** 버린 듣기의 `end` 를 이만큼만 기다린다. 셸이 끝을 못 알리고 사라져도 마이크 버튼이 죽지 않게. */
const DRAIN_TIMEOUT_MS = 3000;

export function useVoiceInput({
  value,
  onChange,
}: {
  value: string;
  onChange: (value: string) => void;
}) {
  const support = useSyncExternalStore(noSubscribe, readSpeechSupport, serverSupport);
  const [listening, setListening] = useState(false);
  /** 셸이 곧 권한 창을 띄운다 — 왜 마이크가 필요한지 먼저 말하는 동안. */
  const [asking, setAsking] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  /** 이 채팅바가 시작한 듣기가 살아 있는가. 거짓이면 오는 글자를 버린다. */
  const activeRef = useRef(false);
  /** 버린 듣기의 `end` 를 기다리는 중인가. 참이면 오는 것을 버리고 새로 시작하지 않는다. */
  const drainingRef = useRef(false);
  const drainTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  /** 누를 때의 글. "들은 말 지우기" 가 여기로 되돌린다. */
  const pressedRef = useRef("");
  /** 들은 말을 붙일 자리 — 누를 때의 글 + 끝난 구간들. */
  const baseRef = useRef("");
  /** 이 훅이 마지막으로 입력창에 넣은 글. 지금 값과 다르면 다른 코드가 넣은 것이다. */
  const writtenRef = useRef("");
  const valueRef = useRef(value);
  const onChangeRef = useRef(onChange);
  useEffect(() => {
    valueRef.current = value;
    onChangeRef.current = onChange;
  });

  const stopDraining = useCallback(() => {
    drainingRef.current = false;
    if (drainTimerRef.current) clearTimeout(drainTimerRef.current);
    drainTimerRef.current = null;
  }, []);

  /** 듣기를 버린다 — 셸에 멈춤을 보내고 그 듣기의 `end` 까지 오는 것을 버린다. 입력창은 건드리지 않는다. */
  const release = useCallback(() => {
    if (!activeRef.current) return;
    activeRef.current = false;
    setListening(false);
    setAsking(false);
    drainingRef.current = true;
    drainTimerRef.current = setTimeout(stopDraining, DRAIN_TIMEOUT_MS);
    getSpeechBridge()?.stop();
  }, [stopDraining]);

  useEffect(
    () =>
      subscribeSpeech((event) => {
        if (drainingRef.current) {
          // 기다리는 동안은 새로 시작하지 않으므로 여기 오는 `start` 는 버린 듣기다 — 셸이 권한을 확인하는 사이에 보냈을 때.
          if (event.type === "start") getSpeechBridge()?.stop();
          if (event.type === "end" || event.type === "error") stopDraining();
          return;
        }
        switch (event.type) {
          case "asking":
            if (activeRef.current) setAsking(true);
            return;
          case "start":
            if (!activeRef.current) getSpeechBridge()?.stop();
            setAsking(false);
            return;
          case "volume":
            // 음량 막대가 직접 듣는다 (`VoiceLevelMeter`) — 채팅바 전체를 0.1초마다 다시 그리지 않게.
            return;
          case "result": {
            if (!activeRef.current) return;
            const next = appendSpoken(baseRef.current, event.text);
            if (event.final) baseRef.current = next;
            writtenRef.current = next;
            onChangeRef.current(next);
            return;
          }
          case "end":
            activeRef.current = false;
            setListening(false);
            setAsking(false);
            return;
          case "error":
            activeRef.current = false;
            setListening(false);
            setAsking(false);
            setNotice(SPEECH_NOTICE[event.reason]);
            return;
        }
      }),
    [stopDraining],
  );

  // 다른 코드가 입력창에 글을 넣었다 — 그 글을 덮지 않게 듣기를 버린다 (머리말).
  useEffect(() => {
    if (activeRef.current && value !== writtenRef.current) release();
  }, [value, release]);

  // 🚨 화면을 떠나면 마이크를 끈다. 보호자가 안 보는 화면이 듣고 있으면 안 된다.
  //    컴포넌트가 내려갈 때와 문서가 통째로 내려갈 때(`pagehide`) 둘 다다.
  useEffect(() => {
    const onPageHide = () => {
      if (activeRef.current) getSpeechBridge()?.stop();
      activeRef.current = false;
    };
    window.addEventListener("pagehide", onPageHide);
    return () => {
      window.removeEventListener("pagehide", onPageHide);
      onPageHide();
      stopDraining();
    };
  }, [stopDraining]);

  /** 듣기 시작. */
  const start = useCallback(() => {
    const bridge = getSpeechBridge();
    if (!bridge?.available || drainingRef.current || activeRef.current) return;
    activeRef.current = true;
    pressedRef.current = valueRef.current;
    baseRef.current = valueRef.current;
    writtenRef.current = valueRef.current;
    setNotice(null);
    setListening(true);
    bridge.start();
  }, []);

  /** 다 말했어요 — 셸에 멈춤을 보낸다. `end` 로 끝난다(마지막 구간은 올 수도 안 올 수도 있다 · `SpeechBridge.stop`). */
  const finish = useCallback(() => {
    if (activeRef.current) getSpeechBridge()?.stop();
  }, []);

  /** 들은 말 지우기 — 듣기를 버리고 입력창을 누를 때의 글로 되돌린다. */
  const discard = useCallback(() => {
    if (!activeRef.current) return;
    release();
    onChangeRef.current(pressedRef.current);
  }, [release]);

  /**
   * 보내기 직전에 부른다 — 안내를 지운다. 듣는 동안은 보내는 길이 없어서 `release` 는 막아 두는 것일 뿐이다
   * (보내는 길이 다시 생겨도 보낸 뒤 비운 입력창을 늦은 글자가 채우지 않게).
   */
  const cancel = useCallback(() => {
    setNotice(null);
    release();
  }, [release]);

  return { support, listening, asking, notice, start, finish, discard, cancel };
}
