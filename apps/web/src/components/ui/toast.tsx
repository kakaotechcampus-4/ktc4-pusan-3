"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import type { ReactNode } from "react";

/**
 * 디자인 시스템 §7 토스트 — **화면 안에 자리가 없는 사실**을 잠깐 띄운다.
 *
 * §14 가 "어떤 성공을 알려야 하는지가 안 정해졌다" 로 미뤄 두었던 자리다. 정한 규칙은 아래.
 *
 * 🚨 **성공을 토스트로 알리지 않는다.** 이 제품의 성공은 이미 화면이 말한다 — 승인 결과는
 *    화면 전환으로, 교정 결과는 시트 안의 `cascade` 로, 저장은 목록이 바뀌는 것으로. 거기에
 *    토스트를 얹으면 같은 말을 두 번 하면서 지친 부모의 시선을 한 번 더 뺏는다.
 *
 * 🚨 **되돌릴 것이 있으면 토스트가 아니라 화면 안에 둔다.** 05 의 "접어 둔 제안 N건 · 되돌리기"
 *    처럼 자리를 만든다 — 토스트는 사라지므로 되돌릴 길을 거기 담으면 길이 같이 사라진다.
 *
 * 🚨 **그래서 남는 자리는 하나다: 조용히 되돌아간 실패.** 준비물 체크처럼 낙관적으로 반영했다가
 *    실패해서 원래대로 돌아가는 경우, 화면에는 체크가 풀린 것만 보이고 **왜** 가 어디에도 없다.
 *    (체크박스 옆에는 문장이 들어갈 자리가 없다.) 그 자리를 이 컴포넌트가 받는다.
 *
 * 🚨 **바텀시트 안에서 부르지 않는다.** 시트는 `<dialog>` 의 top layer 라 이 `fixed` 레이어가
 *    시트 **뒤로** 깔린다 — 띄워도 안 보인다. 시트 안의 실패는 시트 안에서 말한다.
 *
 * 🚨 **발화 원문을 싣지 않는다** (최상위 CLAUDE.md §2 개인정보). 무엇이 실패했는지는 말하되
 *    아이에 대해 적은 문장을 그대로 옮기지 않는다.
 *
 * 생김새 —
 * - **위에 붙는다.** 이 앱의 아래쪽은 채팅바 + 이동 바로 이미 192px 이라, 아래에 띄우면
 *   그 둘 중 하나를 덮는다. 위는 제목 자리라 시선이 처음 가는 곳이기도 하다.
 * - **그림자가 없다** (문서 §6 — 그림자는 바텀시트 하나뿐). `line-strong` 1px 이 경계를 만든다.
 * - **등장 애니메이션이 없다** (문서 §8). 하루에 여러 번 뜨는 것이라, 매번 기다리게 하지 않는다.
 * - **한 번에 하나다.** 쌓으면 390px 화면에서 본문이 묻힌다 — 새 메시지가 앞의 것을 덮어쓴다.
 */
const DURATION_MS = 6000;

interface ToastApi {
  /** 🚨 사실 한 문장. 되돌리기 같은 행동을 여기 담지 않는다 (사라지는 자리다). */
  show: (message: string) => void;
}

const ToastContext = createContext<ToastApi | null>(null);

/**
 * 🚨 **`useToast()` 는 실패해도 화면을 죽이지 않는다.** Provider 밖(서버 컴포넌트 트리나
 *    테스트)에서 불러도 조용히 아무것도 안 하는 것이, 알림 하나 때문에 화면이 통째로 터지는
 *    것보다 낫다. 대신 개발 중에는 콘솔로 알린다.
 */
const NOOP: ToastApi = {
  show: (message) => {
    if (process.env.NODE_ENV === "development") {
      console.warn(`[toast] Provider 밖에서 불렀다: ${message}`);
    }
  },
};

export function useToast(): ToastApi {
  return useContext(ToastContext) ?? NOOP;
}

export function ToastProvider({ children }: { children: ReactNode }) {
  const [message, setMessage] = useState<string | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // 🚨 의존성이 비어 있어야 `show` 의 정체성이 안 바뀐다 — 호출부가 effect 안에서 쓰는데
  //    매 렌더 새 함수면 그 effect 가 계속 다시 돈다.
  const show = useCallback((next: string) => {
    if (timer.current) clearTimeout(timer.current);
    setMessage(next);
    timer.current = setTimeout(() => {
      timer.current = null;
      setMessage(null);
    }, DURATION_MS);
  }, []);

  // 언마운트될 때 타이머가 남으면 사라진 컴포넌트에 setState 한다.
  useEffect(
    () => () => {
      if (timer.current) clearTimeout(timer.current);
    },
    [],
  );

  const api = useMemo<ToastApi>(() => ({ show }), [show]);

  return (
    <ToastContext.Provider value={api}>
      {children}
      <Toast message={message} />
    </ToastContext.Provider>
  );
}

function Toast({ message }: { message: string | null }) {
  return (
    // 🚨 라이브 리전은 **늘 DOM 에 있어야** 한다. 메시지와 함께 나타나면 스크린리더가
    //    리전이 생긴 것으로 보고 내용을 읽지 않는 경우가 있다 — 그래서 빈 채로 세워 둔다.
    //    `polite` 인 이유는 읽던 것을 끊을 만큼 급한 내용이 아니기 때문이다 (문서 §10).
    <div
      role="status"
      aria-live="polite"
      className="max-w-content pt-safe-2 pointer-events-none fixed inset-x-0 top-0 z-50 mx-auto px-3 min-[380px]:px-4"
    >
      {/* 🚨 **누를 수 없다.** 닫기 버튼을 달면 "무엇을 하는 버튼인가" 를 라벨로 또 말해야 하고,
          메시지 자체를 버튼으로 만들면 스크린리더가 "…버튼" 이라고만 읽어 무엇이 일어나는지
          알 수 없다. 시간이 지나면 사라지고 그 동안 클릭은 바깥의 `pointer-events-none` 으로
          뒤에 통과한다. 🚨 그래서 되돌리기 같은 **행동을 여기 담지 않는다.** */}
      {message ? (
        <p className="border-line-strong rounded-card bg-surface text-body-sm text-ink border px-4 py-3">
          {message}
        </p>
      ) : null}
    </div>
  );
}
