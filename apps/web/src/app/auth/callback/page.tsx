"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { Card, CardFailed } from "@/components/ui/card";
import { Screen } from "@/components/ui/screen";
import { Spinner } from "@/components/ui/spinner";
import {
  api,
  isApiError,
  isSignupPending,
  qk,
  type AuthExchangeResponse,
  type Me,
} from "@/lib/api";
import {
  clearBind,
  clearProvider,
  consumeReturnPath,
  readBind,
  readProvider,
  rememberConsentCode,
} from "@/lib/auth";
import { useSessionStore } from "@/stores/session";

/**
 * 복귀 지점. 웹·앱 공통이다 — 웹은 서버가 이 경로로 302 하고, 앱은 셸이 커스텀 스킴으로
 * 받은 쿼리를 그대로 이 경로에 실어 웹뷰를 이동시킨다.
 *
 * 🚨 사용자에게 보여줄 내용이 없는 화면이다. 스피너 한 장 띄우고 교환만 한다.
 * 🚨 AuthGate 로 감싸지 않는다 — 토큰을 **얻으러** 오는 화면이라 감싸면 00 으로 튕긴다.
 *
 * 정본: docs/web/kakao-login-v1.md §4-4
 */

/**
 * 🚨 문구는 프론트가 만든다. 서버는 코드만 싣는다 — 문구를 URL 에 실으면 공격자가
 *    프론트 화면에 임의 문구를 띄우는 통로(피싱 문구 주입)가 된다.
 */
const ERROR_MESSAGE: Record<string, string> = {
  invalid_state: "로그인 요청이 만료됐어요. 다시 시도해 주세요.",
  invalid_bind: "로그인 요청이 올바르지 않아요. 다시 시도해 주세요.",
  invalid_client: "로그인 요청이 올바르지 않아요. 다시 시도해 주세요.",
  oauth_provider_error: "카카오 로그인이 지금 응답하지 않아요. 잠시 후 다시 시도해 주세요.",
};

const DEFAULT_ERROR_MESSAGE = "로그인하지 못했어요. 다시 시도해 주세요.";

/** 만료·재사용·bind 불일치를 서버가 의도적으로 뭉갠다. 화면도 하나로 묶는다. */
const HANDOFF_EXPIRED_MESSAGE = "로그인 확인이 만료됐어요. 다시 시도해 주세요.";

/**
 * 교환 결과. `?error=` 는 여기 담지 않는다 — URL 에서 바로 파생되는 값이라
 * effect 에서 setState 로 옮기면 렌더가 한 번 더 돈다.
 */
type Outcome =
  | { kind: "working" }
  /** 기존 회원인데 필수 동의가 남았다. */
  | { kind: "consent_blocked"; scopes: string[] }
  | { kind: "failed"; message: string };

export default function AuthCallbackPage() {
  const router = useRouter();
  const params = useSearchParams();
  const queryClient = useQueryClient();
  const hydrated = useSessionStore((s) => s.hydrated);
  const signIn = useSessionStore((s) => s.signIn);

  const [outcome, setOutcome] = useState<Outcome>({ kind: "working" });

  /**
   * 🚨 교환은 한 번만 실행한다. StrictMode 의 이중 실행으로 1회용 코드를 두 번 소비하면
   *    두 번째가 401 invalid_handoff 가 되어 로그인이 실패한다.
   */
  const exchanged = useRef(false);

  const code = params.get("code");
  const error = params.get("error");

  /**
   * 🚨 취소(`oauth_denied`)는 에러가 아니다 — 사용자가 카카오에서 그만둔 것이라
   *    배너를 띄우지 않고 아래 effect 가 조용히 00 으로 되돌린다.
   * 🚨 받은 코드를 화면에 출력하지 않는다 — 그 순간 문구 주입 통로가 되살아난다.
   */
  const urlErrorMessage =
    error && error !== "oauth_denied" ? (ERROR_MESSAGE[error] ?? DEFAULT_ERROR_MESSAGE) : null;

  useEffect(() => {
    // persist 복구 전에 signIn() 을 부르면 복구가 그 위에 덮어쓴다.
    if (!hydrated || exchanged.current) return;
    exchanged.current = true;

    if (error) {
      // 어느 쪽이든 이 흐름은 끝났다. 남은 핸드오프 흔적을 지운다.
      clearHandoff();
      // 취소는 배너 없이 되돌린다. 그 밖의 코드는 urlErrorMessage 가 화면에서 처리한다.
      if (error === "oauth_denied") router.replace("/");
      return;
    }
    // 코드도 에러도 없다 — 주소를 직접 입력한 경우다. 조용히 되돌린다.
    if (!code) {
      router.replace("/");
      return;
    }

    void exchange(code);

    async function exchange(oneTimeCode: string) {
      const provider = readProvider();
      try {
        const res = await api.post<AuthExchangeResponse>(`/auth/${provider}`, {
          code: oneTimeCode,
          // bind 는 여기서 지우지 않는다 — 신규 가입이 /signup 에서 같은 값을 쓴다.
          bind: readBind(),
        });

        // 🚨 token 유무가 아니라 status 필드 유무로 분기한다.
        if (isSignupPending(res)) {
          // 신규 회원 — 아직 계정이 없다. 동의를 받아야 그때 만들어진다.
          // bind 는 여기서 지우지 않는다: /signup 이 같은 값을 한 번 더 쓴다.
          rememberConsentCode(res.consent_code);
          router.replace("/auth/consent");
          return;
        }

        signIn(res.token, res.expires_in);

        // 🚨 필수 동의가 남아 있으면 다음 화면으로 넘어가지 않는다 (계약서 §04).
        if (res.consent_required.length > 0) {
          setOutcome({ kind: "consent_blocked", scopes: res.consent_required });
          return;
        }

        clearHandoff();

        const returnPath = consumeReturnPath();
        if (returnPath) {
          router.replace(returnPath);
          return;
        }

        const me = await queryClient.fetchQuery({
          queryKey: qk.me(),
          queryFn: () => api.get<Me>("/me"),
        });
        const first = me.children[0];
        router.replace(first ? `/child/${first.child_id}/home` : "/onboarding");
      } catch (cause) {
        clearHandoff();
        if (isApiError(cause, "invalid_handoff")) {
          setOutcome({ kind: "failed", message: HANDOFF_EXPIRED_MESSAGE });
          return;
        }
        setOutcome({
          kind: "failed",
          message: cause instanceof Error ? cause.message : DEFAULT_ERROR_MESSAGE,
        });
      }
    }
  }, [hydrated, code, error, router, queryClient, signIn]);

  const failure = urlErrorMessage ?? (outcome.kind === "failed" ? outcome.message : null);

  return (
    <Screen className="justify-center gap-4">
      {outcome.kind === "working" && !failure ? (
        <p
          className="text-body text-ink-muted flex items-center justify-center gap-2"
          aria-live="polite"
        >
          <Spinner />
          로그인하는 중…
        </p>
      ) : null}

      {outcome.kind === "consent_blocked" ? (
        <Card>
          <p className="text-body text-ink">먼저 동의가 필요해요</p>
          <p className="text-body-sm text-ink-muted mt-2">남은 동의 {outcome.scopes.length}건</p>
          <p className="text-caption text-ink-subtle mt-1">{outcome.scopes.join(", ")}</p>
          <p className="text-caption text-ink-subtle mt-2">
            동의 화면은 아직 없어요 (10 설정). 그때까지 여기서 멈춥니다.
          </p>
        </Card>
      ) : null}

      {failure ? (
        <>
          <CardFailed>{failure}</CardFailed>
          <button
            type="button"
            className="text-button text-brand min-h-touch"
            onClick={() => router.replace("/")}
          >
            처음으로 돌아가기
          </button>
        </>
      ) : null}
    </Screen>
  );
}

/** 로그인이 끝났거나 더 진행할 수 없을 때만 핸드오프 흔적을 지운다. */
function clearHandoff(): void {
  clearBind();
  clearProvider();
}
