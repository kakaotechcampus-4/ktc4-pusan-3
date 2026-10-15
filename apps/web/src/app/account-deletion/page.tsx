import type { Metadata } from "next";

import { Card } from "@/components/ui/card";
import { PageTitle } from "@/components/ui/page-title";
import { Screen } from "@/components/ui/screen";
import { PRIVACY_POLICY_HREF } from "@/lib/consent";

export const metadata: Metadata = {
  title: "계정 삭제 안내",
};

/**
 * 계정 삭제 안내 — Play Console 데이터 보안 양식의 "계정 삭제 URL" 이 가리키는 페이지다 (#166).
 *
 * 스토어 등록정보에 그대로 걸린다. 앱을 이미 지운 사람도 계정을 지울 수 있게 하려는 칸이라
 * Google 이 이 페이지에 세 가지를 요구한다 — ① 앱 · 개발자 이름 ② 삭제를 요청하는 단계를
 * 눈에 띄게 ③ 지워지는 데이터와 남는 데이터 · 그 기간.
 *
 * 🚨 **로그인 없이 열린다** (`AuthGate` 를 씌우지 않는다). 탈퇴했거나 로그인할 수 없는 사람이
 *    읽는 페이지다. 그래서 API 를 부르지 않고, 서버 컴포넌트로 둔다 — 상태도 훅도 없다.
 * 🚨 **처리방침에 없는 사실을 새로 쓰지 않는다.** 아래 문장은 전부 처리방침 제1장 ③ · 제5장 ·
 *    제9장을 옮긴 것이다. 기간이 바뀌면 처리방침이 먼저 바뀌고, 이 페이지는 그 판에 맞춰 고친다 —
 *    어긋나면 처리방침이 정본이라 맨 아래에 그 링크를 둔다.
 * 🚨 **앱 안에서 이 페이지로 오는 길은 만들지 않는다.** 앱 안의 길은 설정 › 계정 › 탈퇴하기다.
 */
const SERVICE_EMAIL = "parkhajh0803.dev@gmail.com";

const DELETED_NOW = [
  "계정 정보 — 카카오 회원번호, 부르는 이름",
  "개인 기록 — 일기, 지난 대화 기록",
  "직접 등록한 아이 — 관리 권한을 넘기지 않고 탈퇴하면 그 아이와 공동 기록(아이 정보, 일상 기록, 알레르기와 건강 정보, 키와 몸무게, 일정)을 지우고, 함께 보던 보호자와의 연결도 끊어요",
  "함께 보는 보호자로 연결된 아이 — 남기신 공동 기록은 아이 기록으로 남고, 기록에 적힌 작성자는 비워요",
];

const KEPT_FOR_A_WHILE = [
  { data: "백업 사본", period: "최대 30일 뒤 전체 파기" },
  { data: "접속 기록, 오류 기록, 인공지능 요청 기록", period: "3개월 뒤 삭제" },
  { data: "동의 기록 (언제 무엇에 동의했는지)", period: "삭제한 때부터 1년 뒤 파기" },
  { data: "삭제 요청 · 문의 기록", period: "처리를 마친 날부터 1년" },
  { data: "외부 처리 회사에 남은 사본", period: "각 회사가 정한 기간 (처리방침 제7장)" },
];

export default function AccountDeletionPage() {
  return (
    <Screen className="gap-8">
      <header>
        <PageTitle>계정 삭제 안내</PageTitle>
        <p className="text-body-sm text-ink-muted mt-2">
          아이캐치(운영: 트루독)의 계정과 기록을 지우는 방법이에요.
        </p>
      </header>

      <section className="flex flex-col gap-3">
        <h2 className="text-label text-brand">지우는 방법</h2>
        <Card>
          <h3 className="text-section text-ink">앱에서 바로</h3>
          <p className="text-body-sm text-ink mt-2">설정 › 계정 › 탈퇴하기</p>
          <p className="text-body-sm text-ink-muted mt-2">
            탈퇴하면 계정을 바로 쓸 수 없고, 되살릴 수 없어요.
          </p>
        </Card>
        <Card>
          <h3 className="text-section text-ink">앱을 쓸 수 없을 때</h3>
          <p className="text-body-sm text-ink mt-2">
            <a
              href={`mailto:${SERVICE_EMAIL}`}
              className="decoration-line-strong -my-3 inline-block py-3 break-all underline decoration-1 underline-offset-4"
            >
              {SERVICE_EMAIL}
            </a>
          </p>
          <p className="text-body-sm text-ink-muted mt-2">
            앱을 이미 지웠거나 로그인할 수 없어도 이메일로 요청할 수 있어요. 본인인지 확인하는 데
            필요한 것만 여쭙고, 요청을 받은 날부터 10일 안에 처리해요.
          </p>
        </Card>
      </section>

      {/* 🚨 **"데이터 삭제 URL" 칸도 이 페이지를 가리킨다.** 계정은 두고 기록만 지우는 길이다.
          공동 기록의 "잘못된 기록이에요" 는 숨기기라 서버에 남는다(처리방침 제5장 ④) — 그래서
          여기 적지 않고, 서버에서 지우는 길은 이메일 요청이다(제9장 ④). */}
      <section className="flex flex-col gap-3">
        <h2 className="text-label text-brand">계정은 두고 기록만 지우고 싶을 때</h2>
        <Card>
          <p className="text-body-sm text-ink">일기는 앱에서 바로 지울 수 있어요.</p>
          <p className="text-body-sm text-ink-muted mt-2">
            공동 기록을 서버에서 완전히 지우고 싶으면 위 이메일로 요청해 주세요. 요청을 받은 날부터
            10일 안에 처리해요.
          </p>
        </Card>
      </section>

      <section className="flex flex-col gap-3">
        <h2 className="text-label text-brand">탈퇴하면 바로 지워지는 것</h2>
        <Card>
          <ul className="flex flex-col gap-2">
            {DELETED_NOW.map((item) => (
              <li key={item} className="text-body-sm text-ink">
                {item}
              </li>
            ))}
          </ul>
          <p className="border-line text-caption text-ink-subtle mt-3 border-t pt-3">
            탈퇴할 때 카카오에도 이 서비스와의 연결을 끊어 달라고 요청해요.
          </p>
        </Card>
      </section>

      <section className="flex flex-col gap-3">
        <h2 className="text-label text-brand">잠시 남는 것</h2>
        <Card>
          <dl className="flex flex-col gap-3">
            {KEPT_FOR_A_WHILE.map((row) => (
              <div key={row.data}>
                <dt className="text-body-sm text-ink">{row.data}</dt>
                <dd className="text-body-sm text-ink-muted">{row.period}</dd>
              </div>
            ))}
          </dl>
        </Card>
      </section>

      <p className="text-caption text-ink-subtle">
        자세한 기준은{" "}
        <a
          href={PRIVACY_POLICY_HREF}
          target="_blank"
          rel="noopener noreferrer"
          className="text-ink-muted decoration-line-strong -my-3 inline-block py-3 underline decoration-1 underline-offset-4"
        >
          개인정보 처리방침
        </a>{" "}
        제1장 ③과 제5장에 있어요.
      </p>
    </Screen>
  );
}
