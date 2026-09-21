/**
 * 같은 대상에 대한 요청을 **한 줄로 세운다.**
 *
 * 왜 필요한가 — 낙관적 업데이트를 쓰는 토글(준비물 체크)은 응답을 기다리지 않는다. 그래서 체크 →
 * 해제를 빠르게 누르면 `true` · `false` 두 요청이 동시에 나가고, 응답이 역전되면 **마지막 선택이
 * 아닌 값이 서버에 남는다.** 뒤이은 재조회가 그 값을 받아 화면까지 되돌린다 (PR #71 리뷰).
 *
 * 🚨 **버튼을 잠그는 것으로 대신하지 않는다.** 왕복을 기다리는 체크박스로는 가방을 못 싼다
 *    (`calendar-day.tsx` 의 낙관적 업데이트 주석). 화면은 즉시 바뀌고 **요청만** 순서를 지킨다.
 *
 * 🚨 한 줄에 세우는 단위는 **대상 하나**다. 항목이 다르면 서로 기다릴 이유가 없다 —
 *    준비물 A 를 저장하는 동안 준비물 B 가 막히면 그건 다른 버그다.
 */

export interface SerialQueue {
  /** 앞 요청이 끝난 뒤에 실행한다. 돌려주는 Promise 는 이 요청의 결과다 (실패도 그대로 전달). */
  run: <T>(task: () => Promise<T>) => Promise<T>;
  /** 아직 끝나지 않은 요청 수 — 실행 중 + 대기 중. 0 이면 이 줄의 마지막이 끝난 것이다. */
  readonly pending: number;
}

export function createSerialQueue(): SerialQueue {
  let tail: Promise<unknown> = Promise.resolve();
  let pending = 0;

  return {
    run<T>(task: () => Promise<T>): Promise<T> {
      pending += 1;

      // 🚨 앞 요청이 **실패해도** 다음은 나가야 한다. 하나가 터졌다고 줄을 멈추면 그 뒤의
      //    사용자 선택이 영영 전달되지 않는다. 그래서 성공·실패 양쪽에서 이어 붙인다.
      const result = tail.then(task, task);

      // 줄의 꼬리는 결과를 삼킨 쪽으로 잇는다 — 여기서 reject 가 새면 처리되지 않은 거부가 된다.
      tail = result.then(
        () => undefined,
        () => undefined,
      );

      // 호출자가 받는 Promise 는 감소가 끝난 뒤에 완료된다. 그래야 `pending === 0` 을 보는
      // 쪽(재조회 판정)이 자기 차례에 정확한 값을 읽는다.
      return result.finally(() => {
        pending -= 1;
      });
    },

    get pending() {
      return pending;
    },
  };
}
