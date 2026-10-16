"use client";

import { useMutation } from "@tanstack/react-query";
import { ArrowLeft, Check } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";

import { AuthGate } from "@/components/auth-gate";
import { ScreenTransition } from "@/components/route-transition";
import { Button, ButtonLink } from "@/components/ui/button";
import { Card, CardFailed } from "@/components/ui/card";
import { ICON_SIZE, ICON_STROKE } from "@/components/ui/icon";
import { IconButtonLink } from "@/components/ui/icon-button";
import { PageTitle } from "@/components/ui/page-title";
import { Screen } from "@/components/ui/screen";
import { Spinner } from "@/components/ui/spinner";
import { TextArea } from "@/components/ui/text-area";
import { useChildId } from "@/hooks/use-child-id";
import {
  api,
  ApiError,
  isApiError,
  REPORT_CATEGORIES,
  REPORT_MESSAGE_MAX_LENGTH,
  type ReportCategory,
  type ReportRequest,
  type ReportResponse,
} from "@/lib/api";
import { cn } from "@/lib/cn";
import { SERVICE_EMAIL } from "@/lib/consent";
import { BACK } from "@/lib/view-transition";

/**
 * 10 설정 › 문제 알리기 — **흐름 중인 화면이다** (#487 · 서버 PR #488).
 *
 * Google Play 정책(AI 생성 콘텐츠 · 사용자 제작 콘텐츠)은 불쾌한 AI 답 · 공동 기록 · 다른 사용자를
 * **앱을 떠나지 않고** 알릴 길을 요구한다. `mailto:` 는 메일 앱으로 넘어가서 그 길이 못 된다 —
 * 그래서 이 화면이 적은 것을 서버(`POST /reports`)로 바로 보낸다.
 *
 * 🚨 **하단 네비를 붙이지 않는다** (탈퇴와 같다 · `docs/web/components-v1.md` §1). 적는 도중에
 *    새는 길이 있으면 적던 글이 그 자리에서 사라진다. 나가는 길은 위 화살표 하나다.
 *
 * 🚨 **승인 게이트가 아니다.** 게이트는 캘린더 쓰기 · 건강 기록 확정 딱 2곳이다 (최상위 §2) —
 *    `btn-approve` 도 `caution` 도 쓰지 않는다. 되돌릴 수 없는 5곳도 아니라 `Idempotency-Key` 가 없다.
 *
 * 🚨 **적은 글을 남기지 않는다.** 이 컴포넌트의 상태(`useState`)와 보내는 동안의 요청 캐시에만
 *    있고(`gcTime: 0` — 화면을 떠나면 바로 버린다), 보내거나 화면을 떠나면 사라진다. 콘솔에 찍지 않고,
 *    `persist` 스토어 · `localStorage` · `sessionStorage` 로 옮기지 않는다.
 *    `new Error()` 에도 싣지 않는다 (`docs/web/api-client-v1.md` §4). 서버도 DB 에만 두고 로그에 안 찍는다.
 *
 * 🚨 **열람 · 삭제를 요청하는 공식 창구가 아니다.** 이 양식은 답을 보내는 길이 없어서, 답이 필요한
 *    요청은 처리방침에 적힌 이메일로 보내 달라고 **적기 전에** 말한다 (머리 아래 한 줄).
 */
export default function ReportPage() {
  return (
    <AuthGate>
      <ScreenTransition>
        <ReportScreen />
      </ScreenTransition>
    </AuthGate>
  );
}

/** 화면에 서는 이름. 🚨 서버 값(`category`)과 화면 말은 다르다 — 코드는 계약 이름 그대로 둔다. */
const CATEGORY_LABEL: Record<ReportCategory, string> = {
  ai_content: "AI 답이나 추천이 부적절해요",
  shared_record: "공동 기록이나 다른 보호자에게 문제가 있어요",
  bug: "앱이 이상하게 동작해요",
  other: "기타",
};

function ReportScreen() {
  const childId = useChildId();
  const [sent, setSent] = useState(false);

  return (
    <Screen className="gap-6">
      <header>
        {/* 🚨 **화살표는 링크다** (`IconButtonLink`). 줄 전체를 `-ml-3` 로 미는 이유는 탈퇴 ·
            고객센터와 같다 (`docs/web/components-v1.md` §1). */}
        <div className="-ml-3 flex items-center gap-1">
          <IconButtonLink href={`/child/${childId}/settings`} label="설정으로 돌아가기" {...BACK}>
            <ArrowLeft aria-hidden size={ICON_SIZE.md} strokeWidth={ICON_STROKE} />
          </IconButtonLink>
          <PageTitle>문제 알리기</PageTitle>
        </div>
        {sent ? null : (
          <>
            <p className="text-body-sm text-ink-muted mt-2">
              이상하거나 불편했던 일을 알려 주세요.
            </p>
            <p className="text-caption text-ink-subtle mt-2">
              내 정보 열람이나 삭제처럼 답이 필요한 요청은 이메일(
              <a
                href={`mailto:${SERVICE_EMAIL}`}
                className="decoration-line-strong -my-3.5 inline-block py-3.5 break-all underline decoration-1 underline-offset-4"
              >
                {SERVICE_EMAIL}
              </a>
              )로 보내 주세요.
            </p>
          </>
        )}
      </header>

      {sent ? <ReportSent childId={childId} /> : <ReportForm onSent={() => setSent(true)} />}
    </Screen>
  );
}

function ReportForm({ onSent }: { onSent: () => void }) {
  const categoryName = useId();
  const [category, setCategory] = useState<ReportCategory | null>(null);
  const [message, setMessage] = useState("");
  /**
   * 🚨 **두 번 보내지 않는다.** `isPending` 은 다음 렌더에야 바뀌어서, 빠르게 두 번 누르면 두 번째
   *    클릭도 아직 `false` 를 본다. 누른 순간을 이 ref 가 잡는다.
   */
  const sending = useRef(false);

  const report = useMutation({
    mutationFn: (body: ReportRequest) => api.post<ReportResponse>("/reports", body),
    // 🚨 오프라인이어도 바로 보낸다. 기본값("online")은 요청을 보내지도 실패시키지도 않고 멈춰서,
    //    버튼이 "보내는 중이에요" 에 머문다. 보내 보고 못 닿으면 "확인하지 못했어요" 카드가 선다.
    networkMode: "always",
    // 🚨 보낸 글(`variables`)을 요청 캐시에 두지 않는다 — 기본은 화면을 떠난 뒤에도 5분 들고 있다.
    gcTime: 0,
    onSuccess: onSent,
    onSettled: () => {
      sending.current = false;
    },
  });

  /**
   * 🚨 **NUL(`\u0000`)은 떼고 보낸다.** 서버가 400 으로 거절하는 글자라(Postgres 가 받지 못한다),
   *    다른 앱에서 붙여 넣은 글에 섞여 오면 다시 눌러도 계속 막힌다. 화면에는 보이지 않는 글자다.
   */
  const trimmed = message.split("\u0000").join("").trim();
  /**
   * 🚨 **하루 한도에 걸리면 보내기를 잠근다** (429 `too_many_reports`). 누를 때마다 같은 응답이라
   *    "다시 시도" 를 세우지 않는 것과 같은 이유다 (`docs/web/api-client-v1.md` §3 `daily_input_limit`).
   *    적은 글은 그대로 둔다 — 이메일로 옮겨 적을 수 있어야 한다.
   */
  const limited = isApiError(report.error, "too_many_reports");
  const ready = category !== null && trimmed.length > 0 && !limited;

  function submit() {
    if (!ready || category === null || sending.current) return;
    sending.current = true;
    // 서버도 앞뒤 공백을 떼고 센다. 뗀 것을 보내서 화면이 센 것과 서버가 센 것이 같게 한다.
    report.mutate({ category, message: trimmed });
  }

  return (
    <form
      className="flex flex-col gap-6"
      onSubmit={(event) => {
        event.preventDefault();
        submit();
      }}
    >
      {/* 🚨 **네이티브 라디오다** (`choice-field` 와 같은 처리 · 디자인 시스템 §7 고르는 칸).
          `ChoiceField` 를 쓰지 않는 이유는 둘이다 — 선택지가 넷이고 길어서 가로로 똑같이 나눌 수
          없고, 기본값이 없다(아무거나 미리 골라 두면 분류가 틀린 채로 쌓인다). 그렇다고 `Select` 로
          감추면 넷을 다 읽고 고를 수 없다. 그래서 같은 모양의 칸을 **세로로** 쌓고, 처음엔 아무것도
          고르지 않은 채로 둔다 — 빈 값을 선택지로 넣지는 않는다. */}
      <fieldset className="flex min-w-0 flex-col" disabled={report.isPending}>
        <legend className="text-label text-ink-muted">어떤 일인가요?</legend>
        <div className="mt-1.5 flex flex-col gap-2">
          {REPORT_CATEGORIES.map((value) => {
            const selected = value === category;
            return (
              <label
                key={value}
                className={cn(
                  "rounded-field min-h-field ease-standard flex cursor-pointer items-center gap-2 border px-3.5 py-2 transition-colors duration-120",
                  // 🚨 포커스 링은 숨은 `<input>` 이 아니라 이 상자에 그린다 (`ChoiceField` 와 같다).
                  "has-focus-visible:outline-brand has-focus-visible:outline-2 has-focus-visible:outline-offset-2",
                  selected
                    ? "bg-brand-soft border-brand text-brand-ink"
                    : "bg-surface border-line-strong text-ink-muted hover:bg-surface-muted hover:border-ink-subtle active:bg-surface-muted active:border-ink-subtle",
                )}
              >
                <input
                  type="radio"
                  name={categoryName}
                  value={value}
                  checked={selected}
                  onChange={() => setCategory(value)}
                  className="sr-only"
                />
                {/* 🚨 고른 것을 색 하나로 말하지 않는다 — 체크가 같이 선다. 안 고른 칸에도 늘 그려 두고
                    투명하게 둔다(고를 때 글자가 밀리지 않게 · `ChoiceField` 와 같다). */}
                <Check
                  aria-hidden
                  size={ICON_SIZE.sm}
                  strokeWidth={ICON_STROKE}
                  className={cn("shrink-0", selected ? null : "text-transparent")}
                />
                <span className="text-body-sm">{CATEGORY_LABEL[value]}</span>
              </label>
            );
          })}
        </div>
      </fieldset>

      <div className="flex flex-col gap-1.5">
        <TextArea
          label="어떤 일이었는지 적어 주세요"
          hint="어느 화면에서, 어떤 내용이었는지 적어 주시면 확인하기 쉬워요. 1000자까지 적을 수 있어요. 아이의 실명이나 연락처는 적지 말아 주세요."
          value={message}
          onChange={(event) => setMessage(event.target.value)}
          maxLength={REPORT_MESSAGE_MAX_LENGTH}
          readOnly={report.isPending}
        />
        {/* 브라우저의 `maxLength` 와 같은 단위(UTF-16)로 센다 — 막히는 자리와 숫자가 어긋나지 않게. */}
        <p className="text-caption text-ink-subtle text-right">
          <span className="sr-only">글자 수 </span>
          {message.length}/{REPORT_MESSAGE_MAX_LENGTH}
        </p>
      </div>

      <div className="flex flex-col gap-3">
        {/* 실패할 때마다 새로 세운다(`key`) — 다시 눌러 또 실패해도 포커스가 카드로 온다. */}
        {report.isError ? <ReportFailed key={report.submittedAt} error={report.error} /> : null}
        <Button
          type="submit"
          block
          disabled={!ready || report.isPending}
          aria-busy={report.isPending}
        >
          {report.isPending ? <Spinner /> : null}
          {report.isPending ? "보내는 중이에요" : "보내기"}
        </Button>
      </div>
    </form>
  );
}

/**
 * 보내기 실패 자리. 🚨 **빨강으로 칠하지 않는다** (`CardFailed` 가 중립 면이다 · 디자인 시스템 §7).
 * 🚨 **토스트를 쓰지 않는다** — 조용히 되돌아간 실패가 아니라 보호자가 방금 누른 것의 결과다.
 *
 * 🚨 **서버가 거절한 것과 응답을 못 받은 것을 같은 말로 하지 않는다** (탈퇴 화면과 같은 구분).
 *    응답만 잃었으면 이미 전해졌을 수 있다 — "보내지 못했어요" 라고 단정하지 않는다.
 *    다시 눌러 두 번 쌓여도 되돌릴 수 없는 일이 아니라서 다시 누르게 한다.
 * 🚨 **포커스를 카드로 옮긴다.** 보내는 동안 버튼이 꺼지면서 포커스가 `<body>` 로 떨어져, 두면
 *    키보드 사용자가 맨 위부터 다시 지나와야 한다 (보낸 뒤 안내와 같은 처리).
 */
function ReportFailed({ error }: { error: Error }) {
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    box.current?.focus();
  }, []);

  return (
    <div ref={box} tabIndex={-1}>
      <ReportFailedMessage error={error} />
    </div>
  );
}

function ReportFailedMessage({ error }: { error: Error }) {
  if (isApiError(error, "too_many_reports")) {
    // 문구는 서버 것을 그대로 쓴다 — 횟수 · 기간은 서버 설정값이다. 서버 문구가 "이메일로" 라고만
    // 해서, 키보드가 올라온 채로 맨 위까지 올라가지 않게 주소를 카드 안에 한 번 더 적는다.
    // 🚨 링크가 아니라 글자다 — 맨 위 안내에 같은 이름의 링크가 이미 있다 (한 화면에 같은 이름의
    //    링크를 둘 두지 않는다 · `docs/web/components-v1.md` §1).
    return (
      <CardFailed>
        <p>{error.message}</p>
        <p className="mt-1 break-all">{SERVICE_EMAIL}</p>
      </CardFailed>
    );
  }

  // 화면이 미리 막는 것(빈 글 · 길이 · NUL)을 지나 서버가 거절했다 — 다시 눌러도 같으니 고치게 한다.
  if (isApiError(error, "validation_failed")) {
    return (
      <CardFailed>
        <p>보낼 수 없는 내용이 섞여 있어요. 적은 내용은 그대로 있어요.</p>
        <p className="mt-1">내용을 고쳐 다시 보내 주세요.</p>
      </CardFailed>
    );
  }

  if (error instanceof ApiError && error.status < 500) {
    return (
      <CardFailed>
        <p>보내지 못했어요. 적은 내용은 그대로 있어요.</p>
        <p className="mt-1">잠시 뒤 다시 눌러 주세요.</p>
      </CardFailed>
    );
  }

  return (
    <CardFailed>
      <p>전해졌는지 확인하지 못했어요. 적은 내용은 그대로 있어요.</p>
      <p className="mt-1">연결을 확인하고 다시 눌러 주세요.</p>
    </CardFailed>
  );
}

/**
 * 보낸 뒤. 양식을 이 안내로 **갈아 끼운다** — 적은 글은 양식과 함께 사라진다.
 *
 * 🚨 **아래 링크의 이름이 화살표와 다르다** ("마치고 돌아가기" · 화살표는 "설정으로 돌아가기").
 *    한 화면에 같은 이름의 링크를 둘 두지 않는다 (`docs/web/components-v1.md` §1) — 탈퇴의
 *    "그만두고 돌아가기" 처럼 흐름을 끝낸다는 뜻이 이름에 있다.
 * 🚨 **포커스를 안내로 옮긴다.** 누른 버튼이 양식째 사라져서, 두면 포커스가 `<body>` 로 떨어지고
 *    스크린리더에는 무엇이 됐는지가 안 읽힌다.
 */
function ReportSent({ childId }: { childId: string }) {
  const notice = useRef<HTMLDivElement>(null);
  useEffect(() => {
    notice.current?.focus();
  }, []);

  return (
    <>
      <Card>
        <div ref={notice} tabIndex={-1} role="status">
          <p className="text-body text-ink">알려 주셔서 고마워요. 확인하고 필요하면 조치할게요.</p>
        </div>
      </Card>
      <ButtonLink href={`/child/${childId}/settings`} variant="secondary" block {...BACK}>
        마치고 돌아가기
      </ButtonLink>
    </>
  );
}
