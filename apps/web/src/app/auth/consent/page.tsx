"use client";

import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { ConsentChecklist, requiredConsentsChecked } from "@/components/consent-checklist";
import { Button } from "@/components/ui/button";
import { CardFailed } from "@/components/ui/card";
import { PageTitle } from "@/components/ui/page-title";
import { Screen } from "@/components/ui/screen";
import { Spinner } from "@/components/ui/spinner";
import { api, type AuthSession, type AuthSignupRequest } from "@/lib/api";
import {
  clearBind,
  clearConsentCode,
  clearProvider,
  clearSignupNickname,
  readBind,
  readConsentCode,
  readProvider,
  readSignupNickname,
} from "@/lib/auth";
import { ACCOUNT_SIGNUP_CONSENTS, CONSENT_POLICY_VERSION, type ConsentScope } from "@/lib/consent";
import { useSessionStore } from "@/stores/session";

/**
 * 가입 2/2 — 계정 동의. **여기를 통과해야 계정이 만들어진다.**
 *
 * 🚨 **아이 동의(`child_basic` · `child_health`)는 이 화면에 없다** (#96). 그 둘은 01 아이
 *    만들기 화면이 아이 정보와 **한 트랜잭션**으로 보낸다 — 동의를 아이 단위로 기록하기로
 *    하면서 `child_id` 없이는 저장할 수 없게 됐고, 그 id 는 아이를 만들어야 생긴다.
 *    부수 효과로 **초대로 들어온 보호자와 아이를 등록하는 보호자가 이 화면을 공유한다.**
 *    (왜 이 화면에 두지 않는지는 `lib/consent.ts` 의 `ACCOUNT_SIGNUP_CONSENTS` 주석.)
 *
 * 🚨 이 화면에 다른 입력을 섞지 않는다 (docs/web/kakao-login-v1.md §4-5). 보호자 이름을
 *    앞 화면에서 받는 것이 그래서다.
 */
export default function AuthConsentPage() {
  const router = useRouter();
  const signIn = useSessionStore((s) => s.signIn);
  const hydrated = useSessionStore((s) => s.hydrated);

  /** 🚨 선택 동의도 기본값은 **꺼짐**이다. 미리 체크해 두면 "고르지 않음" 이 동의가 된다. */
  const [checked, setChecked] = useState<Partial<Record<ConsentScope, boolean>>>({});
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  /** 가입 대기표와 이름이 있어야 이 화면이 성립한다 (주소 직접 입력 · 새로고침). */
  const consentCode = useRef<string | null>(null);
  const nickname = useRef<string | null>(null);
  useEffect(() => {
    if (!hydrated) return;
    consentCode.current = readConsentCode();
    nickname.current = readSignupNickname();
    if (!consentCode.current) {
      router.replace("/");
      return;
    }
    // 이름만 없는 것은 되돌릴 수 있다 — 대기표는 살아 있으니 앞 화면으로만 보낸다.
    if (!nickname.current) router.replace("/auth/profile");
  }, [hydrated, router]);

  const requiredChecked = requiredConsentsChecked(ACCOUNT_SIGNUP_CONSENTS, checked);

  async function submit() {
    const code = consentCode.current;
    const name = nickname.current;
    if (!code || !name || !requiredChecked) return;

    setPending(true);
    setError(null);
    try {
      const body: AuthSignupRequest = {
        consent_code: code,
        bind: readBind(),
        nickname: name,
        // 🚨 **고른 것만 보낸다.** 선택을 안 고른 스코프까지 실어 보내면 화면이 물어본
        //    것과 서버에 남는 것이 달라진다.
        consents: ACCOUNT_SIGNUP_CONSENTS.filter((i) => checked[i.scope] === true).map((i) => ({
          scope: i.scope,
          policy_version: CONSENT_POLICY_VERSION,
        })),
      };
      const session = await api.post<AuthSession>(`/auth/${readProvider()}/signup`, body);
      signIn(session.token, session.expires_in);

      // 대기표·bind·이름은 여기까지가 마지막 쓰임이다. 계정이 생긴 뒤에는 서버가 아는 값이다.
      clearConsentCode();
      clearBind();
      clearProvider();
      clearSignupNickname();

      // 🚨 방금 만든 계정이라 아이가 없다. 아이를 만들지 초대를 받을지는 다음 화면이 묻는다 —
      //    여기서 01 로 바로 보내면 초대받은 사람이 같은 아이를 또 등록하게 된다 (#96).
      router.replace("/start");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "동의를 저장하지 못했어요.");
    } finally {
      setPending(false);
    }
  }

  return (
    <Screen className="gap-6">
      <div>
        <p className="text-label text-ink-subtle">가입 · 2 / 2</p>
        <PageTitle className="mt-2">
          시작하기 전에
          <br />
          확인해 주세요
        </PageTitle>
        <p className="text-body text-ink-muted mt-3">
          보호자 계정에 대한 동의 두 가지예요. 아이에 대한 동의는 아이를 등록할 때 따로 받아요.
        </p>
      </div>

      <ConsentChecklist
        items={ACCOUNT_SIGNUP_CONSENTS}
        checked={checked}
        onChange={(scope, next) => setChecked((prev) => ({ ...prev, [scope]: next }))}
      />

      <div className="mt-auto flex flex-col gap-3 pt-2">
        {error ? <CardFailed>{error}</CardFailed> : null}

        <Button block onClick={submit} disabled={!requiredChecked || pending}>
          {pending ? <Spinner /> : null}
          {pending ? "저장하는 중…" : "동의하고 시작하기"}
        </Button>
        <p className="text-caption text-ink-subtle text-center">
          필수에 동의하지 않으면 계정이 만들어지지 않아요.
        </p>
      </div>
    </Screen>
  );
}
