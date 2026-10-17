"use client";

import { ArrowLeft, MessagesSquare } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useLayoutEffect, useRef, useState } from "react";

import { AuthGate } from "@/components/auth-gate";
import { ChatThread, chatDayAnchor } from "@/components/chat-thread";
import { ScreenTransition } from "@/components/route-transition";
import { EventDraftList } from "@/components/event-draft-list";
import { HomeComposer } from "@/components/home-composer";
import { PhotoSourceSheet } from "@/components/photo-source-sheet";
import { BottomSheet } from "@/components/ui/bottom-sheet";
import { Button } from "@/components/ui/button";
import { Card, CardFailed } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { ICON_SIZE, ICON_STROKE } from "@/components/ui/icon";
import { IconButtonLink } from "@/components/ui/icon-button";
import { PageTitle } from "@/components/ui/page-title";
import { Screen } from "@/components/ui/screen";
import { SkeletonBlock } from "@/components/ui/skeleton";
import { useChatHistory } from "@/hooks/use-chat-history";
import { useChildId } from "@/hooks/use-child-id";
import { api, qk, type Agent, type Me } from "@/lib/api";
import { useSeoulToday } from "@/hooks/use-seoul-today";
import { formatDay, parseISODate } from "@/lib/format";
import { BACK, FORWARD } from "@/lib/view-transition";
import {
  isBusy,
  sendLine,
  stopAnswering,
  useConversation,
  type ChatQuestion,
  type ChatTurn,
} from "@/stores/conversation";
import { useDraftStore, useDraftText } from "@/stores/draft";
import { usePhotoDraftStore } from "@/stores/photo-draft";

/**
 * 04 대화 — 한 줄 입력의 결과가 이어지는 화면 (#226).
 *
 * 🚨 **별도 주소인 이유는 뒤로가기다.** 웹뷰의 기기 뒤로가기가 히스토리 기반이라
 *    ([`apps/mobile/App.tsx`](../../../../../../mobile/App.tsx)), 홈 위에 레이어로 덮으면 대화 중
 *    뒤로가기가 앱 밖으로 나갈 수 있다 (11-1 이 라우트인 것과 같은 이유).
 * 🚨 **흐름 화면이라 하단 네비를 붙이지 않는다.** 돌아가기는 머리의 화살표 하나다 —
 *    `router.back()` 이 아니라 링크다 (알림 · 링크로 바로 들어오면 돌아갈 히스토리가 없다).
 * 🚨 **이 화면은 아무것도 들고 있지 않다.** 보내기 · 구독 · 끝 처리가 전부 `stores/conversation.ts`
 *    에 있다 — 홈에서 보내자마자 이리로 넘어오고, 여기서 홈에 갔다 와도 결과가 빠짐없이 그려져야
 *    해서다. 예전 04 가 별도 라우트를 못 가졌던 이유(언마운트에 스트림과 원문이 같이 죽는다)가
 *    그렇게 풀렸다.
 *
 * ## 지난 대화 (#228)
 *
 * 🚨 **앱을 다시 켜면 오늘 대화를 서버에서 다시 그린다.** 대화 스토어는 메모리 전용이라 비어 있고, 서버가
 *    남긴 턴(`useChatHistory`)을 이 앱에서 보낸 줄과 `run_id` 로 합쳐 그린다 (`ChatThread` 머리말).
 * 🚨 **위로 올리면 더 이른 대화를 붙인다** — 같은 날의 앞부분, 그다음 대화가 있었던 전날. 붙이는 동안 보고 있던
 *    줄이 제자리에 있어야 한다 (아래 `anchor`). 화면이 짧아 올릴 수 없을 때를 위해 "이전 대화 보기" 도 세운다.
 * 🚨 **`?date=` 로 열면 그날 자리로 내린다** (09 캘린더 하루 보기의 입구). 그날만 따로 여는 화면이 아니다 —
 *    오늘부터 그날까지 이어 받아 같은 대화에 둔다. 따로 열면 거기서 보낸 한 줄이 며칠을 건너뛰고 붙는다.
 */
export default function ChatPage() {
  return (
    // 🚨 `useSearchParams()` 는 Suspense 경계 안에 있어야 한다 (09 캘린더와 같은 이유).
    <Suspense fallback={null}>
      <AuthGate>
        <ChatScreen />
      </AuthGate>
    </Suspense>
  );
}

/**
 * 맨 위에서 이만큼 안쪽이면 더 이른 대화를 부른다. 맨 위에 닿은 뒤에야 부르면 버튼이 먼저 보이고 나서
 * 한 박자 늦게 붙는다 — 버튼 한 줄 높이보다 조금 넉넉하게 둔다.
 */
const LOAD_OLDER_THRESHOLD_PX = 120;

function ChatScreen() {
  const childId = useChildId();
  const router = useRouter();
  const conversation = useConversation(childId);
  const busy = isBusy(conversation);
  const history = useChatHistory(childId);

  /**
   * 캘린더 하루 보기에서 들어온 날. 🚨 형식만 본다 — 날짜를 계산하지 않고 서버가 준 `date` 와 글자로 비교한다.
   * 잘못된 값이면 날짜 없이 연 것과 같다.
   */
  const dateParam = useSearchParams().get("date");
  const target = dateParam && parseISODate(dateParam) ? dateParam : null;

  // 🚨 이름은 스토어에 캐시하지 않는다. 개인정보는 화면이 필요할 때 Query 로 가져온다 (§3).
  const me = useQuery({ queryKey: qk.me(), queryFn: () => api.get<Me>("/me") });
  const nickname = me.data?.children.find((c) => c.child_id === childId)?.nickname;
  /**
   * 머리에 세우는 날. 🚨 **마지막 한 줄의 날이다** — 앱을 켜 둔 채 자정을 넘기면 "오늘" 과 화면에 있는
   * 대화의 날이 다르다. 대화가 없을 때만 오늘(한국 시간)이다. 🚨 `today` 도 자정을 따라 바뀐다.
   */
  const today = useSeoulToday();
  const shownDay = conversation.turns.at(-1)?.day ?? history.turns.at(-1)?.day ?? today;

  // 🚨 `useState` 가 아니다 — 홈과 같은 입력창이다. 네비로 다녀와도 쓰던 글이 살아 있다.
  const [text, setText] = useDraftText(childId);
  const clearDraft = useDraftStore((s) => s.clearDraft);

  /** 일정 초안 시트가 보여주는 답. 🚨 시트는 하나다 — 답마다 하나씩 두면 닫힌 `<dialog>` 가 쌓인다. */
  const [draftsOf, setDraftsOf] = useState<ChatTurn | null>(null);
  const [photoSheetOpen, setPhotoSheetOpen] = useState(false);
  const putPhoto = usePhotoDraftStore((s) => s.putPhoto);

  /**
   * 마지막 답이 보이게 아래로 내린다. 🚨 **부드러운 스크롤을 쓰지 않는다** — 하루에 여러 번 여는
   * 화면이라 매번 굴러 내려가는 것을 기다리게 된다 (디자인 시스템 §8 "스크롤 애니메이션을 만들지 않는다").
   * 답이 자라는 동안(단계 · 저장 · 초안)에도 따라 내린다.
   */
  const last = conversation.turns.at(-1);
  /**
   * 오늘(첫 장)의 턴 수. 🚨 더 이른 장이 붙는 것으로는 내리지 않는다 — 올려 보던 자리가 바닥으로 튄다.
   * 🚨 캘린더에서 그날로 들어온 화면은 이것으로 내리지 않는다 — 뒤이은 다시 받기에서 오늘 턴이 늘면(서버는 채널을
   *    닫은 뒤에 쓴다) 보던 지난 날에서 바닥으로 튄다. 그 화면은 이 앱에서 보낸 줄로만 내린다.
   */
  const newestCount = target ? 0 : (history.data?.pages[0]?.turns.length ?? 0);
  /**
   * 캘린더에서 고른 날로 이미 내렸는가(또는 받기를 포기했는가). 그 전에는 바닥으로 내리지 않는다.
   * 🚨 **이 화면에서 보낸 줄이 있으면 내린다** — 그날을 찾는 중이거나 못 받았어도 방금 보낸 답은 따라가야 한다.
   */
  const jumped = useRef(false);
  useEffect(() => {
    if (target && !jumped.current && conversation.turns.length === 0) return;
    window.scrollTo({ top: document.documentElement.scrollHeight, behavior: "instant" });
  }, [target, newestCount, conversation.turns.length, last?.send.status, last?.run.status]);

  /**
   * 그날까지 이어 받고 그 자리로 내린다. 대화가 있었던 날만 한 장씩이라, 그 사이 대화한 날 수만큼 부른다.
   * 그날에 대화가 없으면(지웠거나 주소를 손으로 고친 경우) 그보다 이른 장에서 멈추고 내리지 않는다.
   */
  const { data, isError, hasNextPage, isFetchingNextPage, isFetchNextPageError, fetchNextPage } =
    history;
  /**
   * 그날을 찾아 이어 받는 중인가. 🚨 그동안은 빈 상태를 세우지 않는다 — 오늘 대화가 없는 날 캘린더에서
   * 지난 날로 들어오면 첫 장이 비어 와서 "여기서 이어져요" 가 한 번 깜빡인다. 다음 장이 실패하면 멈춘다.
   */
  const seeking =
    target !== null &&
    data !== undefined &&
    !data.pages.some((page) => page.date <= target) &&
    hasNextPage &&
    !isFetchNextPageError;
  useEffect(() => {
    if (!target || jumped.current) return;
    if (!data) {
      // 첫 장부터 못 받았으면 찾기를 그만둔다 — 그래야 이 화면에서 보낸 줄을 따라 내린다.
      if (isError) jumped.current = true;
      return;
    }
    if (seeking) {
      if (!isFetchingNextPage) void fetchNextPage();
      return;
    }
    jumped.current = true;
    document.getElementById(chatDayAnchor(target))?.scrollIntoView({ block: "start" });
  }, [target, data, isError, seeking, isFetchingNextPage, fetchNextPage]);

  /**
   * 더 이른 장을 붙이기 직전의 자리. 🚨 **위에 붙으면 보던 줄이 아래로 밀린다** — 붙은 높이만큼 다시 내려
   * 보던 줄을 제자리에 둔다. 브라우저의 스크롤 고정(`overflow-anchor`)은 웹뷰마다 달라서 직접 한다.
   */
  const anchor = useRef<{ height: number; top: number } | null>(null);
  function loadOlder() {
    if (!hasNextPage || isFetchingNextPage) return;
    anchor.current = { height: document.documentElement.scrollHeight, top: window.scrollY };
    void fetchNextPage();
  }
  const pageCount = data?.pages.length ?? 0;
  useLayoutEffect(() => {
    const before = anchor.current;
    if (!before) return;
    anchor.current = null;
    const grown = document.documentElement.scrollHeight - before.height;
    window.scrollTo({ top: before.top + grown, behavior: "instant" });
  }, [pageCount]);

  /**
   * 위로 올리면 붙인다. 🚨 **사람이 굴린 뒤에만 건다** — 대화가 짧으면 처음부터 맨 위라, 그대로 두면 열자마자
   * 지난 대화를 끝까지 줄줄이 받는다. 바닥으로 내리는 `scrollTo` 도 스크롤 이벤트라 그것과 구분한다.
   * 🚨 **이미 맨 위에서 올리면 스크롤 이벤트가 없다** — 그래서 휠 · 터치 자체도 맨 위인지 본다. 키보드는 건다만
   *    하고 보지 않는다 — 대화가 짧은 화면에서 입력창에 글자를 칠 때마다 지난 날을 받게 된다.
   */
  const loadOlderRef = useRef(loadOlder);
  useEffect(() => {
    loadOlderRef.current = loadOlder;
  });
  useEffect(() => {
    let armed = false;
    const nearTop = () => {
      if (armed && window.scrollY < LOAD_OLDER_THRESHOLD_PX) loadOlderRef.current();
    };
    const pulled = (event: Event) => {
      armed = true;
      // 휠은 방향을 안다 — 맨 위에서 아래로 굴린 것까지 지난 날을 부를 까닭은 없다.
      if (event instanceof WheelEvent && event.deltaY >= 0) return;
      nearTop();
    };
    const arm = () => {
      armed = true;
    };
    window.addEventListener("wheel", pulled, { passive: true });
    window.addEventListener("touchmove", pulled, { passive: true });
    window.addEventListener("keydown", arm);
    window.addEventListener("scroll", nearTop, { passive: true });
    return () => {
      window.removeEventListener("wheel", pulled);
      window.removeEventListener("touchmove", pulled);
      window.removeEventListener("keydown", arm);
      window.removeEventListener("scroll", nearTop);
    };
  }, []);

  const nothingYet = conversation.turns.length === 0 && history.turns.length === 0;

  function send() {
    const sent = sendLine(childId, text, {
      // 🚨 입력창 위에 질문이 떠 있을 때만 답이다 (`AnsweringBar`).
      asReply: conversation.answering !== null,
      source: "home_input",
    });
    // 입력창은 비운다. 🚨 원문은 말풍선이 들고 있다 — 서버가 끝을 말하기 전에는 거기서 안 사라진다.
    if (sent) clearDraft(childId);
  }

  /**
   * 04 "이것도 도와드릴까요?" → 05. 🚨 **run_id 를 싣지 않는다** (#249) — 제안은 이어서 하는 것이 아니라
   * 새로 고른 것이라 홈처럼 처음 요청으로 돈다. 실으면 서버는 그 run 에 이 Agent 기록이 없어
   * "이어서 할 수 없어요" 를 낸다. run_id 는 #227 "후보 보기" 에만 붙인다.
   */
  function goToSuggestions(agents: Agent[]) {
    const params = new URLSearchParams({ agents: agents.join(",") });
    router.push(`/child/${childId}/suggestions?${params.toString()}`, FORWARD);
  }

  return (
    <ScreenTransition>
      <Screen
        className="gap-6"
        topBar={
          // 🚨 **머리를 붙여 둔다.** 대화는 아래로 자라서, 돌아가는 화살표가 본문에 있으면 몇 번만 주고받아도
          //    화면 밖으로 밀린다 — 흐름 화면이라 네비가 없어서 나가는 길이 그것 하나다.
          // 🚨 줄 전체를 `-ml-3` 로 민다 (44px 원 안의 아이콘이 왼쪽 기준선에 맞게 · 10 › 고객센터와 같다).
          <header className="-ml-3 flex items-center gap-1">
            {/* 캘린더에서 왔으면 그날의 캘린더로 돌아간다 — 홈으로 보내면 보던 날을 다시 골라야 한다. */}
            {target ? (
              <IconButtonLink
                href={`/child/${childId}/calendar?date=${target}`}
                label="캘린더로 돌아가기"
                {...BACK}
              >
                <ArrowLeft aria-hidden size={ICON_SIZE.md} strokeWidth={ICON_STROKE} />
              </IconButtonLink>
            ) : (
              <IconButtonLink href={`/child/${childId}/home`} label="홈으로 돌아가기" {...BACK}>
                <ArrowLeft aria-hidden size={ICON_SIZE.md} strokeWidth={ICON_STROKE} />
              </IconButtonLink>
            )}
            <div className="min-w-0">
              {/* 자정을 넘겨 켜 둔 앱에서 어제 대화를 "오늘" 이라고 부르지 않는다. */}
              <PageTitle>{shownDay === today ? "오늘 대화" : "대화"}</PageTitle>
              {/* 누구의 어느 날 대화인지. 🚨 날짜 머리줄은 날이 둘 이상일 때만 본문에 선다
                (`ChatThread`) — 하루뿐이면 이 줄이 그 말을 한다. */}
              <p className="text-caption text-ink-subtle">
                {nickname ? `${nickname} · ${formatDay(shownDay)}` : formatDay(shownDay)}
              </p>
            </div>
          </header>
        }
        bottomBar={
          <div className="flex flex-col gap-2">
            {conversation.answering ? (
              <AnsweringBar
                question={conversation.answering}
                savedAlongside={savedAlongside(conversation.turns, conversation.answering.runId)}
                onStop={() => stopAnswering(childId)}
              />
            ) : null}
            <HomeComposer
              value={text}
              onChange={setText}
              onSubmit={send}
              // 제안 줄은 홈의 것이다 — 서버가 시각대로 고른 "지금 도와드릴 것" 이라 여기서는 안 세운다.
              prompts={[]}
              onPickPrompt={() => {}}
              onPickPhoto={() => setPhotoSheetOpen(true)}
              pending={busy}
              busyLabel="답을 기다리는 중이에요"
            />
          </div>
        }
      >
        {hasNextPage ? (
          <OlderTurns
            loading={isFetchingNextPage}
            failed={isFetchNextPageError}
            onMore={loadOlder}
          />
        ) : null}

        {/* 🚨 **지난 대화를 못 받아도 지금 보낸 줄은 그린다** (NF-06). 실패는 그 위 한 자리에서만 말한다. */}
        {/* 🚨 받아 둔 것이 있으면 다시 받기 실패로 이 카드를 세우지 않는다 — 지난 대화가 그대로 보이는데
            "불러오지 못했어요" 가 선다. run 이 끝날 때마다 다시 받는다 (`qk.child` 무효화). */}
        {isError && !data ? (
          <CardFailed>
            <p>
              지난 대화를 지금 불러오지 못했어요. 적어주신 기록은 기록 화면에 그대로 남아 있어요.
            </p>
            <Button
              variant="secondary"
              size="compact"
              className="mt-3"
              onClick={() => void history.refetch()}
            >
              다시 불러오기
            </Button>
          </CardFailed>
        ) : null}

        {nothingYet ? (
          // 🚨 받는 중에 빈 상태를 먼저 세우지 않는다 — 앱을 다시 켤 때마다 "아무것도 없다" 가 한 번 깜빡인다.
          history.isPending || seeking ? (
            <SkeletonBlock label="대화를 불러오는 중" />
          ) : isError ? null : (
            <EmptyState
              icon={MessagesSquare}
              title="아래에 한 줄 적으면 여기서 이어져요"
              description="주고받은 대화는 날짜별로 남아요. 적어주신 기록은 기록 화면에도 그대로 남아요."
              count={0}
              countLabel="오늘 보낸 한 줄"
            />
          )
        ) : (
          <ChatThread
            childId={childId}
            past={history.turns}
            turns={conversation.turns}
            answering={conversation.answering}
            onOpenDrafts={setDraftsOf}
            onPickOffer={goToSuggestions}
          />
        )}

        {/* 🚨 **승인 게이트 ㉠ 이 이 시트 안의 카드에 있다.** 시트에는 등장 애니메이션이 없다
          (디자인 시스템 §8). 닫아도 초안은 남는다 — 넣지 않은 장은 다시 열면 그대로 선다. */}
        <BottomSheet
          open={draftsOf !== null}
          onClose={() => setDraftsOf(null)}
          // 카드마다의 "넣기" 가 승인 게이트 ㉠ 이다 — 열고 닫는 움직임이 없다 (디자인 시스템 §8 "하지 않는 것").
          gate
          // 🚨 설명을 달지 않는다 — 목록이 자기 머리글("일정으로 만들까요?" · 넣기 전에는 안 들어간다)을
          //    이미 세운다. 시트 머리에도 같은 말을 두면 한 화면에 같은 두 줄이 겹쳐 선다.
          title="찾은 일정"
          // 🚨 **닫는 버튼을 세운다** (디자인 시스템 §7 — 웹뷰에는 ESC 가 없다). 승인 시트(06)와 달리 스크림으로도
          //    닫히는데, 닫아도 초안은 남고 넣는 것은 카드마다의 버튼이라 실수로 닫혀도 잃는 것이 없다 (§7 예외).
          footer={
            <Button variant="secondary" block onClick={() => setDraftsOf(null)}>
              닫기
            </Button>
          }
        >
          {draftsOf ? (
            // 🚨 `key` 로 답마다 새로 그린다 — 안 그러면 앞 답의 넘김 위치 · 넣은 표시가 남는다.
            <EventDraftList
              key={draftsOf.id}
              childId={childId}
              incoming={[]}
              draftIds={draftsOf.run.drafts.map((d) => d.draft_id)}
              origin="input"
              found="적어주신 말에서 일정을 찾았어요."
            />
          ) : null}
        </BottomSheet>

        <PhotoSourceSheet
          open={photoSheetOpen}
          onClose={() => setPhotoSheetOpen(false)}
          onPick={({ file, lane }) => {
            putPhoto(childId, file, lane);
            setPhotoSheetOpen(false);
            router.push(`/child/${childId}/photos`, FORWARD);
          }}
        />
      </Screen>
    </ScreenTransition>
  );
}

/**
 * 입력창이 지금 어느 질문에 답하는지 (#226).
 *
 * 🚨 **답을 강요하지 않는다.** "답하지 않고 새로 적기" 로 내려놓을 수 있고, 내려놓아도 질문은 위
 *    대화에 열린 채로 남아 "이 질문에 답하기" 로 다시 고를 수 있다 (`stores/conversation.ts` 규칙 4).
 * 🚨 **답만 적으라고 말한다.** 앞서 적은 말을 다시 쓰게 하면 이미 저장된 조각이 또 저장된다
 *    (#158 리뷰) — 앞 이야기는 서버가 `reply_to` 로 찾는다.
 * 🚨 `accent` 카드가 아니다. 이 화면에서 브랜드 테두리를 받을 "지금 볼 것" 은 답 묶음이다.
 */
function AnsweringBar({
  question,
  savedAlongside,
  onStop,
}: {
  question: ChatQuestion;
  /** 그 질문을 낸 run 이 저장한 것이 있는가. */
  savedAlongside: boolean;
  onStop: () => void;
}) {
  return (
    <Card>
      <p className="text-caption text-ink-subtle">위 질문에 답하는 중</p>
      {/* 🚨 LLM 이 만든 문장이라 HTML 로 그리지 않는다 (apps/web/CLAUDE.md §4). */}
      <p className="text-body-sm text-ink mt-1">{question.text}</p>
      {/* 🚨 **저장 여부에 따라 말한다** (#231 리뷰). 저장 0건인 질문에 "앞서 적어주신 말은 저장돼 있어요" 를
          세웠더니, 바로 위 답의 "아직 기록으로 남기지 않았어요" 와 한 화면에서 부딪쳤다 (예전엔 03 · 04 로
          화면이 갈려서 안 보였다). 앞 이야기는 저장 여부와 상관없이 서버가 `reply_to` 로 찾는다. */}
      <p className="text-caption text-ink-subtle mt-2">
        {savedAlongside
          ? "이 질문에 대한 답만 적어주세요. 앞서 적어주신 말 중 기록할 것은 저장돼 있어요."
          : "이 질문에 대한 답만 적어주세요. 앞서 적어주신 말은 이어서 같이 볼게요."}
      </p>
      <Button variant="tertiary" size="compact" className="mt-3" onClick={onStop}>
        답하지 않고 새로 적기
      </Button>
    </Card>
  );
}

/**
 * 더 이른 대화로 가는 문 (#228). 위로 올리면 저절로 붙지만, 화면이 짧아 올릴 수 없을 때 이 버튼이 길이다.
 * 🚨 다음 장만 실패한 것은 받아 둔 대화를 지우지 않고 여기서 말한다 (07 "기록 더 보기" 와 같다).
 */
function OlderTurns({
  loading,
  failed,
  onMore,
}: {
  loading: boolean;
  failed: boolean;
  onMore: () => void;
}) {
  return (
    <div className="flex flex-col items-center gap-2">
      {failed ? (
        <p role="status" className="text-body-sm text-ink-muted">
          이전 대화를 불러오지 못했어요. 다시 눌러 주세요.
        </p>
      ) : null}
      <Button variant="tertiary" size="compact" onClick={onMore} disabled={loading}>
        {loading ? "불러오는 중" : "이전 대화 보기"}
      </Button>
    </div>
  );
}

/** 그 질문을 낸 run 이 저장한 것이 있는가 — 입력창 위 문장이 이걸로 갈린다 (`AnsweringBar`). */
function savedAlongside(turns: ChatTurn[], runId: string): boolean {
  const asked = turns.find((t) => t.send.status === "sent" && t.send.runId === runId);
  return (asked?.run.observations.length ?? 0) > 0;
}
