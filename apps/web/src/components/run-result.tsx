"use client";

import {
  Hand,
  Hourglass,
  Info,
  MessageCircleQuestion,
  PenLine,
  ShieldCheck,
  Sprout,
  Stethoscope,
  type LucideIcon,
} from "lucide-react";

import { AgentPrompts } from "@/components/agent-prompts";
import { domainLabel, observationAgent } from "@/components/domain-chip";
import { Button, ButtonLink } from "@/components/ui/button";
import { Card, CardFailed } from "@/components/ui/card";
import { DomainIcon } from "@/components/ui/icon";
import { IconTile } from "@/components/ui/icon-tile";
import { ProgressSteps } from "@/components/ui/progress-steps";
import type { RunState } from "@/hooks/use-run-stream";
import type { GuidanceEvent, NoteEvent } from "@/lib/api/sse";
import {
  isHealthObservation,
  type Agent,
  type ConfidenceSource,
  type Observation,
} from "@/lib/api/types";

/**
 * 04 저장 결과 — run 이벤트를 화면으로 옮긴다.
 *
 * 🚨 **별도 라우트가 아니다.** 03 홈과 같은 컴포넌트 트리 안의 레이어다 —
 *    화면을 벗어나면 `useRunStream` 이 스트림을 끊고, 실패 시 입력창에 되돌릴 **원문의 정본이
 *    입력 화면이 들고 있는 값**이기 때문이다 (apps/web/CLAUDE.md §3).
 *
 * 🚨 **`partial` 은 실패 화면이 아니다** (NF-06). Agent 2개 중 1개만 성공해도 그 화면을 보여주고,
 *    성공과 실패를 **한 화면에** 섞는다. `done` 이 와도 `partial` 이 있었으면 부분 결과다.
 * 🚨 **실패를 빨강으로 칠하지 않는다** (문서 §3). `CardFailed` 는 `surface-muted` 다.
 * 🚨 **health 관찰은 모양이 다르다.** `subject` · `polarity` · `affinity` 키 자체가 없다 —
 *    `null` 검사가 아니라 `isHealthObservation()` 으로 분기한다.
 */

/** 진행 중. 단계 문구는 서버가 보낸 `step.label` 을 그대로 쓴다. */
export function RunProgress({ state }: { state: RunState }) {
  return (
    <div className="flex flex-1 flex-col justify-center gap-6">
      <div>
        <IconTile icon={PenLine} className="mb-3" />
        <h2 className="text-title text-ink">적어주신 말을 보고 있어요</h2>
        <p className="text-body-sm text-ink-muted mt-2">
          저장이 끝나야 다음으로 넘어가요. 잘못 저장하지 않으려고 한 칸씩 확인해요.
        </p>
      </div>

      <Card>
        <ProgressSteps
          index={state.step?.index ?? 0}
          total={state.step?.total ?? 3}
          label={state.step?.label ?? "시작하고 있어요"}
        />
      </Card>
    </div>
  );
}

export function RunResult({
  state,
  childId,
  inputText,
  onRetry,
  onEdit,
  onDone,
  onPickOffer,
  onAnswerQuestion,
}: {
  state: RunState;
  /** 안내의 목적지를 아이 경로 아래에 붙이는 데 쓴다 (`GUIDANCE_DESTINATION`). */
  childId: string;
  /** 부모가 적은 원문. 🚨 훅이 아니라 입력 화면이 들고 있는 값이다. */
  inputText: string;
  onRetry: () => void;
  onEdit: () => void;
  onDone: () => void;
  onPickOffer: (agents: Agent[]) => void;
  /** 되묻는 질문에 답하러 03 홈으로. 질문은 홈까지 따라간다 (`stores/pending-question.ts`). */
  onAnswerQuestion: (question: string) => void;
}) {
  // 🚨 서버가 끝을 말하지 않고 끝난 run 이다 (20초 침묵 · 끊긴 스트림 · 연결 실패).
  //    **저장 여부를 모른다** — "저장했어요" 도 "저장하지 않았어요" 도 말하지 않는다
  //    (CLAUDE.md §2 · PR #71 리뷰). 원문은 입력창에 그대로 남아 있다 (03 홈 `closeRun`).
  if (state.status === "unconfirmed") {
    return (
      <div className="flex flex-col gap-4">
        <div>
          <h2 className="text-title text-ink">결과를 받지 못했어요</h2>
          <p className="text-body-sm text-ink-muted mt-2">
            연결이 끊겨서 저장됐는지 확인하지 못했어요. 적어주신 말은 입력창에 그대로 남겨뒀어요.
          </p>
        </div>

        <CardFailed>
          {/* 같은 키로 나가는 재시도라 실제로 두 번 저장되지 않는다 (lib/api/idempotency.ts). */}
          <p>다시 시도하면 같은 한 줄로 확인해요. 이미 저장됐다면 두 번 저장되지 않아요.</p>
        </CardFailed>

        {state.observations.length > 0 ? (
          <section>
            <h3 className="text-label text-brand">
              여기까지 받은 기록 {state.observations.length}건
            </h3>
            <Card className="divide-line mt-2 flex flex-col divide-y">
              {state.observations.map((observation) => (
                <ObservationRow key={observation.id} observation={observation} />
              ))}
            </Card>
          </section>
        ) : null}

        <div className="flex flex-wrap gap-2">
          <Button onClick={onRetry}>다시 시도</Button>
          <Button variant="secondary" onClick={onEdit}>
            직접 고쳐 쓰기
          </Button>
        </div>
      </div>
    );
  }

  if (state.failure) {
    return (
      <div className="flex flex-col gap-4">
        <div>
          <h2 className="text-title text-ink">읽지 못했어요</h2>
          <p className="text-body-sm text-ink-muted mt-2">
            한 줄을 구조화하는 데 실패했어요. 잘못 저장하지 않으려고 아무것도 저장하지 않았어요.
          </p>
        </div>

        <CardFailed>
          <p>적어주신 말은 입력창에 그대로 남겨뒀어요.</p>
        </CardFailed>

        <div className="flex flex-wrap gap-2">
          <Button onClick={onRetry}>다시 시도</Button>
          <Button variant="secondary" onClick={onEdit}>
            직접 고쳐 쓰기
          </Button>
        </div>
      </div>
    );
  }

  const noChildObservation = state.observations.length === 0;
  const savedAnything = state.observations.length > 0 || state.promoted.length > 0;
  /**
   * 🚨 **안내가 이미 이유를 말했으면 "기록을 찾지 못했어요" 를 겹쳐 세우지 않는다.**
   *    "땅콩 알레르기 있어" 는 못 읽은 한 줄이 아니라 **저장하면 안 되는 한 줄**이다 (최상위 §2).
   *    두 장을 나란히 두면 같은 자리에서 서로 다른 이유를 대고, 아래쪽이 위쪽을 부정한다.
   *    Memory 가 되물은 경우도 같다 — 아직 못 정한 것이지 못 읽은 것이 아니다.
   */
  const noticedWhy = state.guidance.length > 0 || state.note !== null;
  /** Memory 가 답을 기다리는 중. 🚨 `kind` 를 모르면 질문으로 보지 않는다 (`NoteCard`). */
  const askingMore = state.note?.kind === "question";

  return (
    <div className="flex flex-col gap-4">
      {/* 🚨 저장된 것이 없으면 "이렇게 저장했어요" 는 **사실이 아니다.** 안내만 나간 run
          (알레르기·진단·구매 대행)과 되묻기만 한 run 이 그렇다 — 제목이 화면 내용을 앞질러
          말하면 아래 카드가 전부 그 말을 뒤집는 꼴이 된다.
          🚨 되묻기만 있는 run 에서는 **제목이 곧 할 일**이다. 화면에 남는 것이 질문 하나뿐이라
             "확인했어요" 로 받으면 다 끝난 것처럼 읽힌다 — 최소 질문 1개라는 규칙(최상위 §2)이
             화면에서는 "한 가지만" 이라는 말로 선다. */}
      <div>
        <h2 className="text-title text-ink">
          {savedAnything
            ? "이렇게 저장했어요"
            : askingMore
              ? "한 가지만 더 알려주세요"
              : "적어주신 말을 확인했어요"}
        </h2>
        {askingMore && !savedAnything ? (
          // 일어난 일만 적는다 — "답하면 저장돼요" 는 예측이라 쓰지 않는다 (§4).
          <p className="text-body-sm text-ink-muted mt-2">
            적어주신 말만으로는 아직 기록으로 남기지 않았어요.
          </p>
        ) : null}
      </div>

      {/* 이 화면의 주인공은 결과 카드가 아니라 **부모가 적은 말**이다 (관찰 노트).
          화면에서 한 장만 쓰는 `card-accent` 를 여기 쓴다 — 아래는 전부 거기서 나온 것이다. */}
      {inputText ? (
        <Card tone="accent">
          <div className="flex items-start gap-3">
            <IconTile icon={PenLine} />
            <div className="min-w-0">
              <p className="text-caption text-ink-subtle">적어주신 한 줄</p>
              <p className="text-body text-ink mt-1">{inputText}</p>
            </div>
          </div>
        </Card>
      ) : null}

      {/* 🚨 **안내를 맨 위에 둔다.** "땅콩 알레르기 있어" 라고 적은 보호자가 제일 먼저 알아야
          하는 것은 **그 말이 저장되지 않았다**는 사실이다 (최상위 §2). 아래 기록 목록보다
          뒤에 두면 스크롤해야 보이고, 그 사이에 부모는 저장된 줄 안다. */}
      {state.guidance.map((guidance) => (
        <GuidanceCard
          key={guidance.code}
          childId={childId}
          guidance={guidance}
          savedAlongside={savedAnything}
        />
      ))}

      {state.unavailable.length > 0 ? (
        <UnavailableCard agents={state.unavailable} savedAlongside={savedAnything} />
      ) : null}

      {state.note ? <NoteCard note={state.note} onAnswer={onAnswerQuestion} /> : null}

      {/* 🚨 서버가 보낸 partial 만 여기 온다 — 어느 Agent 가 실패했는지 서버가 말해 준 경우다.
          클라이언트가 스스로 끝낸 경우는 위 `unconfirmed` 로 빠진다. */}
      {state.partial ? (
        <CardFailed>
          <p>
            {state.partial.failed.map(domainLabel).join(", ")} 쪽은 이번에 준비하지 못했어요. 아래
            결과는 그대로 저장됐어요.
          </p>
        </CardFailed>
      ) : null}

      {noChildObservation ? (
        // 위 `noticedWhy` 주석 — 안내·되묻기가 이미 이유를 말했으면 이 카드를 세우지 않는다.
        noticedWhy ? null : (
          <CardFailed>
            <p>
              아이에 관한 기록은 찾지 못했어요. 적어주신 말은 그대로 두고, 기록으로는 저장하지
              않았어요.
            </p>
          </CardFailed>
        )
      ) : (
        <section>
          <h3 className="text-label text-brand">기록 {state.observations.length}건 저장됨</h3>
          {/* 🚨 관찰마다 카드를 한 장씩 주면 흰 상자가 줄줄이 서서 무엇이 한 덩어리인지 사라진다.
              한 장 안에 가는 선으로 나눈다 — 03 홈의 "오늘" 카드와 같은 방식이다. */}
          <Card className="divide-line mt-2 flex flex-col divide-y">
            {state.observations.map((observation) => (
              <ObservationRow key={observation.id} observation={observation} />
            ))}
          </Card>
        </section>
      )}

      {state.promoted.length > 0 ? (
        <section className="flex flex-col gap-2">
          <h3 className="text-label text-brand">기억이 자랐어요</h3>
          {state.promoted.map((change) => (
            <Card key={change.ref.id}>
              <div className="flex items-start gap-3">
                <IconTile icon={Sprout} />
                <div className="min-w-0">
                  <p className="text-body text-ink">{change.merge_key}</p>
                  <p className="text-body-sm text-ink-muted mt-1">{change.state_reason}</p>
                </div>
              </div>
            </Card>
          ))}
        </section>
      ) : null}

      {/* 저장된 것이 없으면 승격 정책을 말할 자리가 아니다 — 셀 것이 없다. */}
      {savedAnything ? (
        <p className="text-caption text-ink-subtle">
          한 번의 행동은 성향으로 확정하지 않아요. 반복 횟수는 코드가 셉니다.
        </p>
      ) : null}

      {/* 🚨 제안을 버튼으로 쌓지 않는다. 예전에는 [제안][제안][아니요] 3개가 같은 무게로 서서
          무엇이 다음 행동인지가 없었다 — 고르는 것은 줄(`AgentPromptRow`)이고, 화면을 떠나는
          것만 버튼이다. "아니요" 버튼은 지웠다: 기본값이 기록만이라는 것은 아래 문구가 말하고,
          아무것도 고르지 않아도 이미 저장은 끝나 있다. */}
      {state.offers.length > 0 ? (
        <section className="flex flex-col gap-2">
          <h3 className="text-label text-brand">이것도 도와드릴까요?</h3>
          {/* 여기는 화면에 자리가 있으므로 세로로 쌓는다 (03 채팅바 위와 달리 입력창을 안 민다). */}
          <AgentPrompts
            items={state.offers.map((offer) => ({ agent: offer.agent, text: offer.label }))}
            onPick={(agent) => onPickOffer([agent])}
            layout="list"
          />
          <p className="text-caption text-ink-subtle mt-1">
            고르지 않아도 기록은 이미 남았어요. 기본값은 기록만이에요.
          </p>
        </section>
      ) : null}

      <Button variant="secondary" block onClick={onDone}>
        홈으로
      </Button>
    </div>
  );
}

/**
 * 안내 한 장을 그리는 데 필요한 것 — **`code` 로 정하는 것은 이것뿐이다.**
 *
 * 🚨 **`message` 는 여기 없다.** 안내 문장은 정책이라(최상위 §2) 서버가 주는 것을 그대로 쓴다 —
 *    화면이 code 별 문구를 따로 만들면 정책과 화면이 두 곳에서 갈린다. 여기 있는 `label` 은
 *    **어떤 종류의 안내인가**를 붙이는 이름표고, `CONFIDENCE_LABEL` 과 같은 성격의 표다.
 *
 * 🚨 **서버가 준 `deeplink` 를 주소로 쓰지 않는다** (apps/web/CLAUDE.md §3). 서버 문자열이라
 *    지금 없는 경로가 오기도 하고, 외부 주소가 오면 화면이 앱 밖으로 나간다. 딥링크는
 *    **어느 구역인지 힌트**로만 쓰고, 실제로 가는 곳은 이 표가 정한다.
 *
 * ⚠️ **서버 힌트와 실제 자리가 다르다.** `safety_record` 의 딥링크는 `settings/health-safety`
 *    인데, 알레르기·건강 기록을 직접 넣는 구역(`components/safety-section.tsx`)은 10 설정이
 *    아니라 **11 아이 프로필**에 있다. 힌트를 그대로 썼으면 아무것도 없는 화면으로 보냈을 것이다.
 *    👉 이름을 맞출지는 `apps/api` Owner 협의 대상이다 (최상위 §8 · #141).
 *
 * 🚨 `to` 가 없는 안내에는 **버튼을 세우지 않는다** — 진단·구매 대행은 갈 곳이 없는 안내고,
 *    없는 길을 만들어 주는 것보다 말만 하고 마는 쪽이 맞다.
 */
const GUIDANCE_KIND: Record<
  string,
  { label: string; icon: LucideIcon; to?: { path: string; label: string } }
> = {
  safety_record: {
    label: "안전 정보",
    icon: ShieldCheck,
    to: { path: "profile", label: "직접 입력하러 가기" },
  },
  diagnosis: { label: "진단·처방", icon: Stethoscope },
  out_of_scope: { label: "서비스 범위", icon: Hand },
};

/** 모르는 code 가 와도 카드는 선다 — `message` 만 있으면 그릴 수 있다. */
const GUIDANCE_FALLBACK = { label: "안내", icon: Info } as const;

/**
 * "이건 대신 해 드릴 수 없어요" 한 장 (#141).
 *
 * 🚨 **`CardFailed` 가 아니다.** 고장이 아니라 **경계**다 — 알레르기를 LLM 이 저장하지 않는 것은
 *    이 서비스가 그렇게 만들어진 것이고(최상위 §2), 실패로 그리면 다시 시도하면 될 일로 읽힌다.
 *    `ConsentRequiredCard` 가 같은 이유로 `Card` 다.
 *
 * 모양은 위의 "적어주신 한 줄" 카드와 **같은 구조**다 (타일 · 이름표 · 본문). 저장이 0건인 run
 * 에서는 화면에 이 카드 하나만 남는데, 글자 한 덩이만 있으면 무엇을 보는 화면인지 읽히지 않는다 —
 * `IconTile` 은 색을 아껴 쓰는 이 화면에 브랜드를 들여보내라고 만든 자리다 (`icon-tile.tsx`).
 */
function GuidanceCard({
  childId,
  guidance,
  savedAlongside,
}: {
  childId: string;
  guidance: GuidanceEvent;
  /**
   * 같은 run 에서 저장된 것이 있는가. 🚨 **한 줄에 두 얘기가 섞여 들어오는 경우가 실제로 있다** —
   * "계란 잘 먹었어. 그리고 땅콩 알레르기 있어" 면 서버는 앞은 저장하고 뒤는 안내로 돌린다
   * (`pipeline.py` — 안내는 라우팅이 내고 Memory 는 기록 조각만 받는다).
   * 그때 안내 카드만 읽고 **전부 저장 안 된 줄 아는 것**을 막는 자리다.
   */
  savedAlongside: boolean;
}) {
  const kind = GUIDANCE_KIND[guidance.code] ?? GUIDANCE_FALLBACK;
  const to = "to" in kind ? kind.to : undefined;

  return (
    <Card>
      <div className="flex items-start gap-3">
        <IconTile icon={kind.icon} />
        <div className="min-w-0">
          <p className="text-caption text-ink-subtle">{kind.label}</p>
          <p className="text-body text-ink mt-1">{guidance.message}</p>
          {/* `partial` 카드와 같은 말이다 ("아래 결과는 그대로 저장됐어요") — 한 화면에서
              못 한 것과 한 것을 갈라 말하는 방식을 두 벌 만들지 않는다. */}
          {savedAlongside ? (
            <p className="text-body-sm text-ink-muted mt-2">아래 기록은 그대로 저장했어요.</p>
          ) : null}
          {to ? (
            <ButtonLink
              href={`/child/${childId}/${to.path}`}
              variant="secondary"
              size="compact"
              className="mt-3"
            >
              {to.label}
            </ButtonLink>
          ) : null}
        </div>
      </div>
    </Card>
  );
}

/**
 * 아직 없는 Agent 로 간 요청 (#141).
 *
 * 🚨 **`partial` 과 같은 카드를 쓰지 않는다.** `partial` 은 Agent 가 해 보다 **실패한** 것이고,
 *    이건 Agent 가 **아직 없는** 것이다 — 고장이 아니라 알려진 빈칸이라, 회색 각주로 깔아 두면
 *    놀이 추천을 기대한 보호자가 아무 답도 못 받은 것처럼 읽는다. 그래서 안내 카드와 같은
 *    구조(타일 · 이름표 · 본문)로 세운다.
 * 🚨 **타일은 `neutral` 이다.** 안내(`GuidanceCard`)는 보호자가 할 일이 있어서 브랜드 타일이
 *    서지만, 여기는 **할 수 있는 일이 없다** — 같은 색을 주면 눌러 볼 것이 있는 줄 안다.
 * 🚨 **화면을 대체하지 않는다.** 라우팅 직후에 오는 이벤트라 저장과 같은 run 에 실리는데,
 *    실패 화면으로 빼면 방금 저장된 기록이 사라진다 (최상위 §2 · NF-06).
 */
function UnavailableCard({ agents, savedAlongside }: { agents: Agent[]; savedAlongside: boolean }) {
  return (
    <Card>
      <div className="flex items-start gap-3">
        <IconTile icon={Hourglass} tone="neutral" />
        <div className="min-w-0">
          <p className="text-caption text-ink-subtle">아직 준비 중</p>
          {/* 문구는 화면이 만든다 — 서버가 주는 것은 Agent 이름뿐이고, `partial` 과 같은
              `domainLabel` 표를 쓴다. */}
          <p className="text-body text-ink mt-1">
            {agents.map(domainLabel).join(", ")} 쪽은 아직 만들고 있어요. 준비되면 여기에서 바로 쓸
            수 있어요.
          </p>
          {savedAlongside ? (
            <p className="text-body-sm text-ink-muted mt-2">아래 기록은 그대로 저장했어요.</p>
          ) : null}
        </div>
      </div>
    </Card>
  );
}

/**
 * Memory 가 한 말 (#141).
 *
 * 🚨 **`kind` 를 모르면 질문으로 취급하지 않는다** (`sse.ts` 의 `NoteEvent`). 서버가 종류를
 *    실어 주기 전에는 요약 문장까지 "이어서 적기" 를 달게 되고, 화면이 답을 재촉하게 된다.
 * 🚨 **답하라고 떠미는 자리가 아니다.** 버튼은 `secondary` 하나고, 그냥 홈으로 가는 길도
 *    아래에 그대로 있다 — 답하려면 한 줄을 새로 보내야 해서 하루 입력 횟수를 한 번 더 쓴다 (#147).
 * 🚨 **LLM 출력이라 HTML 로 그리지 않는다** (apps/web/CLAUDE.md §4). `{}` 로 넣어 React 가
 *    이스케이프하게 둔다 — `dangerouslySetInnerHTML` 을 쓰지 않는다.
 *
 * "한 가지만 더" 는 최소 질문 1개 규칙(최상위 §2)을 화면에서 말하는 자리이기도 하다 — 질문이
 * 하나뿐이라는 것을 부모가 먼저 알면, 답하는 것이 끝없는 문답처럼 보이지 않는다.
 */
function NoteCard({ note, onAnswer }: { note: NoteEvent; onAnswer: (question: string) => void }) {
  if (note.kind !== "question") {
    // 되묻기가 아닌 말은 안내 문장 한 줄이다 — 이름표도 버튼도 붙이지 않는다.
    return (
      <Card>
        <p className="text-body text-ink">{note.text}</p>
      </Card>
    );
  }

  return (
    <Card>
      <div className="flex items-start gap-3">
        <IconTile icon={MessageCircleQuestion} />
        <div className="min-w-0">
          <p className="text-caption text-ink-subtle">한 가지만 더</p>
          <p className="text-body text-ink mt-1">{note.text}</p>
          {/* 🚨 **앞서 적은 말을 다시 쓰라고 하지 않는다** (#158 리뷰). 원문을 되돌려 이어 적게
              했더니, 일부가 이미 저장된 run 에서는 그 조각이 **두 번 저장**됐다. 앞 이야기는
              서버가 `reply_to` 로 찾으므로 화면은 답만 받는다 (03 홈 `answerQuestion`). */}
          <p className="text-body-sm text-ink-muted mt-2">
            이 질문에 대한 답만 적어주시면 돼요. 앞서 적어주신 말은 그대로 두고 이어서 볼게요.
          </p>
          <Button
            variant="secondary"
            size="compact"
            className="mt-3"
            onClick={() => onAnswer(note.text)}
          >
            이어서 적기
          </Button>
        </div>
      </div>
    </Card>
  );
}

/** 발화의 출처. 서버 enum 을 화면 문구로 옮기는 표다 — 추측을 섞지 않는다. */
const CONFIDENCE_LABEL: Record<ConfidenceSource, string> = {
  institution_notice: "기관 공지",
  parent_direct: "보호자 직접",
  parent_hedged: "보호자 추측",
  parent_hearsay: "전해 들음",
};

function ObservationRow({ observation }: { observation: Observation }) {
  const agent = observationAgent(observation.kind);
  const detail = isHealthObservation(observation)
    ? [observation.domain_fields.symptom?.join(", "), observation.domain_fields.observed_time]
        .filter(Boolean)
        .join(", ")
    : observation.subject;

  return (
    <div className="py-3 first:pt-0 last:pb-0">
      {/* 🚨 여기서는 **도메인 색을 쓰지 않는다** (`DomainChip` 이 아니다). 한 화면에 도메인 색은
          2개까지인데(문서 §3), 한 줄이 관찰 3건으로 갈리면 4색이 다 뜰 수 있다. 그리고 도메인 색은
          "어느 Agent 의 결과인가" 신호라(05 제안) 저장된 관찰에 쓰면 그 뜻이 흐려진다.
          모양은 아이콘이, 뜻은 라벨이 진다. */}
      <div className="text-label text-ink-muted flex flex-wrap items-center gap-1.5">
        <DomainIcon agent={agent} className="text-ink-subtle" />
        {domainLabel(agent)}
        {observation.observed_label ? (
          <span className="text-caption text-ink-subtle">· {observation.observed_label}</span>
        ) : null}
      </div>

      <p className="text-body text-ink mt-2">{observation.raw_text}</p>

      <div className="text-caption text-ink-subtle mt-1.5 flex flex-wrap items-center gap-x-1.5">
        {detail ? <span>{detail}</span> : null}
        {detail ? <span aria-hidden>·</span> : null}
        <span>{CONFIDENCE_LABEL[observation.confidence_source]}</span>
      </div>
    </div>
  );
}
