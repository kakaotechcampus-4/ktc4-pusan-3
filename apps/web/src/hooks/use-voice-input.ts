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
 *
 * 🚨 **듣는 동안 입력창을 고칠 수 없게 한다**(`listening` 으로 `readOnly`). 붙일 자리를 누를 때의 글로 잡아
 *    두므로, 그 사이에 고친 글은 다음 결과가 덮는다.
 * 🚨 **보내면 그 뒤로 오는 글자는 버린다**(`cancel`). 그만 듣기 뒤에도 마지막 구간이 한 번 더 오는데,
 *    보낸 뒤 비운 입력창에 그것이 다시 채워지면 보낸 글이 되살아난 것처럼 보인다.
 * 🚨 **버린 듣기의 `end` 가 오기 전에는 새로 듣지 않는다**(`drainingRef`). 셸 이벤트에는 어느 듣기의 것인지가
 *    없어서, 그 사이에 새로 시작하면 지난 듣기의 늦은 글자가 새 글 뒤에 붙고 늦은 `end` 가 새 듣기를 끈다.
 * 🚨 **이 채팅바가 시작하지 않은 듣기가 시작되면 멈춘다.** 셸은 듣는데 화면은 꺼져 있는 상태를 남기지 않는다.
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
  const [notice, setNotice] = useState<string | null>(null);

  /** 이 채팅바가 시작한 듣기가 살아 있는가. 거짓이면 오는 글자를 버린다. */
  const activeRef = useRef(false);
  /** 버린 듣기의 `end` 를 기다리는 중인가. 참이면 오는 것을 버리고 새로 시작하지 않는다. */
  const drainingRef = useRef(false);
  const drainTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  /** 들은 말을 붙일 자리 — 누를 때의 글 + 끝난 구간들. */
  const baseRef = useRef("");
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
          case "start":
            if (!activeRef.current) getSpeechBridge()?.stop();
            return;
          case "result": {
            if (!activeRef.current) return;
            const next = appendSpoken(baseRef.current, event.text);
            if (event.final) baseRef.current = next;
            onChangeRef.current(next);
            return;
          }
          case "end":
            activeRef.current = false;
            setListening(false);
            return;
          case "error":
            activeRef.current = false;
            setListening(false);
            setNotice(SPEECH_NOTICE[event.reason]);
            return;
        }
      }),
    [stopDraining],
  );

  // 🚨 화면을 떠나면 마이크를 끈다. 보호자가 안 보는 화면이 듣고 있으면 안 된다.
  useEffect(
    () => () => {
      if (activeRef.current) getSpeechBridge()?.stop();
      activeRef.current = false;
      stopDraining();
    },
    [stopDraining],
  );

  const toggle = useCallback(() => {
    const bridge = getSpeechBridge();
    if (!bridge?.available || drainingRef.current) return;
    if (activeRef.current) {
      bridge.stop();
      return;
    }
    activeRef.current = true;
    baseRef.current = valueRef.current;
    setNotice(null);
    setListening(true);
    bridge.start();
  }, []);

  /** 듣기를 버리고 안내를 지운다 — 보내기 직전에 부른다. 이미 들어온 글자는 입력창에 남는다. */
  const cancel = useCallback(() => {
    setNotice(null);
    if (!activeRef.current) return;
    activeRef.current = false;
    setListening(false);
    drainingRef.current = true;
    drainTimerRef.current = setTimeout(stopDraining, DRAIN_TIMEOUT_MS);
    getSpeechBridge()?.stop();
  }, [stopDraining]);

  return { support, listening, notice, toggle, cancel };
}
