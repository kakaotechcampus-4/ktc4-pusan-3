"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { PageTitle } from "@/components/ui/page-title";
import { Screen } from "@/components/ui/screen";
import { TextInput } from "@/components/ui/text-input";
import { readConsentCode, readSignupNickname, rememberSignupNickname } from "@/lib/auth";
import { useSessionStore } from "@/stores/session";

/**
 * 가입 1/2 — 보호자 이름. **신규 회원만 지나는 화면이다.**
 *
 * 🚨 **동의 화면과 합치지 않는다.** 법적 고지를 읽고 확인하는 화면에 무관한 입력이 같은
 *    제출 버튼에 묶이면 "무엇에 동의한 것인가" 가 흐려진다 (docs/web/kakao-login-v1.md §4-5 ·
 *    테크스펙 리스크 ④). 그래서 이름은 앞 화면이 받고, 값은 가입이 끝날 때까지
 *    `lib/auth/signup-nickname.ts` 가 들고 있는다.
 *
 * 🚨 **여기서 계정이 만들어지지 않는다.** 계정은 다음 화면의 필수 동의 뒤에 생긴다
 *    (`auth-kakao-v1.md` §6-1 — 동의하지 않은 계정을 DB 에 남기지 않는다). 그래서 이 화면의
 *    버튼은 "다음" 이지 "가입" 이 아니다.
 *
 * ⚠️ 이름을 `POST /auth/{provider}/signup` 바디로 보내는 것은 **계약 확정 전이다** (#96) —
 *    `AuthSignupRequest.nickname` 주석 참고.
 */
export default function SignupProfilePage() {
  const hydrated = useSessionStore((s) => s.hydrated);

  /**
   * 🚨 복구 전에는 아무것도 그리지 않는다 (`AuthGate` 와 같은 처리). 이 화면은 그 이유가
   *    하나 더 있다 — 아래 폼이 `sessionStorage` 를 **첫 렌더에** 읽는다. 서버 렌더에는
   *    그 저장소가 없어서, 마운트를 여기서 막지 않으면 읽을 자리부터가 없다.
   */
  if (!hydrated) return null;
  return <SignupProfileForm />;
}

/** 아이 별명과 같은 상한. 한쪽만 길면 목록에서 줄이 어긋난다. */
const MAX_LENGTH = 20;

function SignupProfileForm() {
  const router = useRouter();

  /** 되돌아왔을 때 적어 둔 이름을 그대로 보여준다 — 다시 타이핑하게 만들지 않는다. */
  const [nickname, setNickname] = useState(() => readSignupNickname() ?? "");
  const [error, setError] = useState<string | null>(null);

  /** 가입 대기표가 없으면 이 화면에 올 이유가 없다 (주소 직접 입력 등). */
  useEffect(() => {
    if (!readConsentCode()) router.replace("/");
  }, [router]);

  function submit() {
    const value = nickname.trim();
    if (!value) {
      setError("부르는 이름을 알려주세요.");
      return;
    }
    rememberSignupNickname(value);
    router.push("/auth/consent");
  }

  return (
    <Screen className="gap-6">
      <div>
        <p className="text-label text-ink-subtle">가입 · 1 / 2</p>
        <PageTitle className="mt-2">
          어떻게
          <br />
          불러드릴까요
        </PageTitle>
        <p className="text-body text-ink-muted mt-3">
          아이 이름이 아니라 <strong className="text-ink">보호자 이름</strong>이에요. 아이는 다음에
          등록해요.
        </p>
      </div>

      <TextInput
        label="내 이름"
        hint="실명이 아니어도 돼요. 화면에서 이렇게 불러요."
        value={nickname}
        onChange={(e) => {
          setNickname(e.target.value);
          if (error) setError(null);
        }}
        maxLength={MAX_LENGTH}
        autoComplete="off"
        error={error}
      />

      <div className="mt-auto flex flex-col gap-3 pt-2">
        <Button block onClick={submit}>
          다음
        </Button>
        <p className="text-caption text-ink-subtle text-center">
          아직 계정이 만들어지지 않았어요. 다음 화면에서 동의하면 만들어져요.
        </p>
      </div>
    </Screen>
  );
}
