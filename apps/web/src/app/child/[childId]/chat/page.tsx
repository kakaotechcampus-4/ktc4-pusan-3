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
import type { PastTurn } from "@/lib/chat-history";
import { useChildId } from "@/hooks/use-child-id";
import { useLocationConsent } from "@/hooks/use-consents";
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
 * 🚨 **`?date=` 로 열면 그날부터 연다** (09 캘린더 하루 보기의 입구 · 서버 `next_date` · #228 리뷰). 위로 올리면
 *    전날이, 아래로 내리면 다음 날이 붙는다 — 한동안 오늘부터 그날까지 하루씩 이어 받았는데, 한 달 전을 고르면
 *    대화한 날 수만큼 요청이 순서대로 나갔다.
 *    🚨 **오늘까지 내려오기 전에는 이 앱에서 보낸 줄을 그리지 않는다** — 그날 아래에 며칠을 건너뛰고 오늘 줄이 붙는다.
 *    그래서 이 화면에서 한 줄 보내면 오늘부터 연 대화로 넘어간다 (`send`).
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
 * 맨 위(또는 맨 아래)에서 이만큼 안쪽이면 더 이른(늦은) 대화를 부른다. 끝에 닿은 뒤에야 부르면 버튼이 먼저
 * 보이고 나서 한 박자 늦게 붙는다 — 버튼 한 줄 높이보다 조금 넉넉하게 둔다.
 */
const LOAD_MORE_THRESHOLD_PX = 120;

/**
 * 머리 아래 이만큼까지 올라온 날을 "보고 있는 날" 로 친다. 날짜 머리줄이 바 바로 아래, 곧 날 구역을 맨 위로
 * 올렸을 때 서는 자리(`ChatThread` 의 `scroll-mt-24`)에 오면 그날로 친다. 머리 높이와 그 자리 사이를 덮는 값이라
 * `scroll-mt-*` 를 바꾸면 이것도 같이 본다.
 */
const DAY_LINE_SLACK_PX = 48;

/**
 * `line`(화면 위에서부터의 거리) 위로 올라간 날 중 가장 늦은 날. 아직 아무 날도 올라가지 않았으면 첫 날이고,
 * 그려진 날이 없으면 `null` 이다. 날짜는 `ChatThread` 가 단 그대로 읽는다 — 여기서 계산하지 않는다.
 */
function dayUnder(line: number): string | null {
  const sections = document.querySelectorAll<HTMLElement>("[data-chat-day]");
  let found: string | null = sections[0]?.dataset.chatDay ?? null;
  for (const section of sections) {
    if (section.getBoundingClientRect().top > line) break;
    found = section.dataset.chatDay ?? found;
  }
  return found;
}

function ChatScreen() {
  const childId = useChildId();
  /** 위치 동의가 켜져 있으면 입력에 흐린 위치를 싣는다 (#593 · `lib/location.ts`). */
  const shareLocation = useLocationConsent(childId);
  const router = useRouter();
  const conversation = useConversation(childId);
  const busy = isBusy(conversation);

  /**
   * 캘린더 하루 보기에서 들어온 날. 🚨 형식만 본다 — 날짜를 계산하지 않는다. 잘못된 값이면 날짜 없이 연 것과 같다.
   */
  const dateParam = useSearchParams().get("date");
  const target = dateParam && parseISODate(dateParam) ? dateParam : null;
  const history = useChatHistory(childId, target);
  const {
    data,
    isError,
    hasNextPage,
    isFetchingNextPage,
    isFetchNextPageError,
    fetchNextPage,
    hasPreviousPage,
    isFetchingPreviousPage,
    isFetchPreviousPageError,
    fetchPreviousPage,
  } = history;
  /**
   * 이 앱에서 보낸 줄을 그리는가 — 지난 날부터 연 화면은 오늘까지 내려온 뒤에만 (위 머리말).
   * 🚨 **받기 전에도 숨긴다** — 받기 전에는 `hasPreviousPage` 가 거짓이라, 그것만 보면 그날을 보러 왔는데
   *    오늘 줄이 먼저 섰다가 사라진다. 그동안은 스켈레톤이다.
   */
  const liveTurns =
    !target || (data !== undefined && !hasPreviousPage) ? conversation.turns : NO_TURNS;

  // 🚨 이름은 스토어에 캐시하지 않는다. 개인정보는 화면이 필요할 때 Query 로 가져온다 (§3).
  const me = useQuery({ queryKey: qk.me(), queryFn: () => api.get<Me>("/me") });
  const nickname = me.data?.children.find((c) => c.child_id === childId)?.nickname;
  /**
   * 머리에 세우는 날. 🚨 **지금 머리 바로 아래에 걸린 날이다** (`docs/web/chat-run-v1.md` §1 · #541) — 위로 올려
   * 전날을 붙이거나 캘린더에서 지난 날부터 열면 한 화면에 여러 날이 있어서, 마지막 한 줄의 날로 고정하면 보고 있는
   * 대화와 머리의 날짜가 어긋난다. 아직 재지 못했으면 그날부터 연 날, 아니면 마지막 한 줄의 날이다. 대화가 없을
   * 때만 오늘(한국 시간)이다.
   */
  const today = useSeoulToday();
  const topBarRef = useRef<HTMLElement>(null);
  const [seenDay, setSeenDay] = useState<string | null>(null);
  const shownDay = seenDay ?? target ?? liveTurns.at(-1)?.day ?? history.turns.at(-1)?.day ?? today;
  useEffect(() => {
    const measure = () => {
      const line = (topBarRef.current?.getBoundingClientRect().bottom ?? 0) + DAY_LINE_SLACK_PX;
      setSeenDay(dayUnder(line));
    };
    // 붙은 줄이 그려지고 그날 자리로 내린 뒤에 잰다.
    const frame = requestAnimationFrame(measure);
    window.addEventListener("scroll", measure, { passive: true });
    window.addEventListener("resize", measure);
    return () => {
      cancelAnimationFrame(frame);
      window.removeEventListener("scroll", measure);
      window.removeEventListener("resize", measure);
    };
  }, [history.turns, liveTurns]);

  // 🚨 `useState` 가 아니다 — 홈과 같은 입력창이다. 네비로 다녀와도 쓰던 글이 살아 있다.
  const [text, setText] = useDraftText(childId);
  const clearDraft = useDraftStore((s) => s.clearDraft);

  /**
   * 일정 초안 시트가 보여주는 답의 초안. 🚨 시트는 하나다 — 답마다 하나씩 두면 닫힌 `<dialog>` 가 쌓인다.
   * 지난 턴도 연다 — 보관함에 아직 남은 초안이면 이어서 넣을 수 있다 (`ChatThread` 의 `PastTurnItem`).
   */
  const [draftsOf, setDraftsOf] = useState<{ key: string; draftIds: string[] } | null>(null);
  const [photoSheetOpen, setPhotoSheetOpen] = useState(false);
  const putPhoto = usePhotoDraftStore((s) => s.putPhoto);

  /**
   * 마지막 답이 보이게 아래로 내린다. 🚨 **부드러운 스크롤을 쓰지 않는다** — 하루에 여러 번 여는
   * 화면이라 매번 굴러 내려가는 것을 기다리게 된다 (디자인 시스템 §8 "스크롤 애니메이션을 만들지 않는다").
   * 답이 자라는 동안(단계 · 저장 · 초안)에도 따라 내린다.
   */
  const last = conversation.turns.at(-1);
  /** 오늘(첫 장)의 턴 수. 🚨 더 이른 장이 붙는 것으로는 내리지 않는다 — 올려 보던 자리가 바닥으로 튄다. */
  const newestCount = data?.pages[0]?.turns.length ?? 0;
  useEffect(() => {
    // 지난 날부터 연 화면은 그날 자리에 둔다 — 거기서 보내면 오늘 대화로 넘어가서 여기서 따라 내릴 줄이 없다.
    if (target) return;
    window.scrollTo({ top: document.documentElement.scrollHeight, behavior: "instant" });
  }, [target, newestCount, conversation.turns.length, last?.send.status, last?.run.status]);

  /** 지난 날부터 연 화면은 처음 받았을 때 그날 자리로 한 번 내린다. */
  const jumped = useRef(false);
  useEffect(() => {
    if (!target || jumped.current || !data) return;
    jumped.current = true;
    document.getElementById(chatDayAnchor(target))?.scrollIntoView({ block: "start" });
  }, [target, data]);

  /**
   * 더 이른 장을 붙이기 직전의 자리. 🚨 **위에 붙으면 보던 줄이 아래로 밀린다** — 붙은 높이만큼 다시 내려
   * 보던 줄을 제자리에 둔다. 브라우저의 스크롤 고정(`overflow-anchor`)은 웹뷰마다 달라서 직접 한다.
   * 더 늦은 장은 아래에 붙어서 보던 줄이 밀리지 않는다 — 가장 이른 장이 바뀐 때만 맞춘다.
   */
  const anchor = useRef<{ height: number; top: number } | null>(null);
  /** 🚨 한 번에 한 방향만 — 짧은 날을 밀면 위아래가 같이 걸리는데, 진행 중인 쪽을 다른 쪽이 취소한다. */
  const loadingMore = isFetchingNextPage || isFetchingPreviousPage;
  function loadOlder() {
    if (!hasNextPage || loadingMore) return;
    anchor.current = { height: document.documentElement.scrollHeight, top: window.scrollY };
    void fetchNextPage();
  }
  function loadNewer() {
    if (!hasPreviousPage || loadingMore) return;
    void fetchPreviousPage();
  }
  const oldestPage = data?.pages.at(-1);
  useLayoutEffect(() => {
    const before = anchor.current;
    if (!before) return;
    anchor.current = null;
    const grown = document.documentElement.scrollHeight - before.height;
    window.scrollTo({ top: before.top + grown, behavior: "instant" });
  }, [oldestPage]);

  /**
   * 위로 올리면 더 이른 날을, 아래로 내리면 더 늦은 날을 붙인다(지난 날부터 연 화면만).
   * 🚨 **사람이 굴린 뒤에만 건다** — 대화가 짧으면 처음부터 맨 위라, 그대로 두면 열자마자 지난 대화를 끝까지
   *    줄줄이 받는다. 바닥으로 내리는 `scrollTo` 도 스크롤 이벤트라 그것과 구분한다.
   * 🚨 **이미 끝에서 더 밀면 스크롤 이벤트가 없다** — 그래서 휠 · 터치 자체도 끝인지 본다. 키보드는 걸기만
   *    하고 보지 않는다 — 대화가 짧은 화면에서 입력창에 글자를 칠 때마다 다른 날을 받게 된다.
   */
  const loadRef = useRef({ loadOlder, loadNewer });
  useEffect(() => {
    loadRef.current = { loadOlder, loadNewer };
  });
  useEffect(() => {
    let armed = false;
    const nearTop = () => {
      if (armed && window.scrollY < LOAD_MORE_THRESHOLD_PX) loadRef.current.loadOlder();
    };
    const nearBottom = () => {
      const below = document.documentElement.scrollHeight - window.scrollY - window.innerHeight;
      if (armed && below < LOAD_MORE_THRESHOLD_PX) loadRef.current.loadNewer();
    };
    const onScroll = () => {
      nearTop();
      nearBottom();
    };
    const pulled = (event: Event) => {
      armed = true;
      // 휠은 방향을 안다 — 위로 굴렸으면 위만, 아래로 굴렸으면 아래만 본다.
      if (event instanceof WheelEvent) {
        if (event.deltaY < 0) nearTop();
        else if (event.deltaY > 0) nearBottom();
        return;
      }
      onScroll();
    };
    const arm = () => {
      armed = true;
    };
    window.addEventListener("wheel", pulled, { passive: true });
    window.addEventListener("touchmove", pulled, { passive: true });
    window.addEventListener("keydown", arm);
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => {
      window.removeEventListener("wheel", pulled);
      window.removeEventListener("touchmove", pulled);
      window.removeEventListener("keydown", arm);
      window.removeEventListener("scroll", onScroll);
    };
  }, []);

  const nothingYet = liveTurns.length === 0 && history.turns.length === 0;

  function send() {
    const sent = sendLine(childId, text, {
      // 🚨 입력창 위에 질문이 떠 있을 때만 답이다 (`AnsweringBar`).
      asReply: conversation.answering !== null,
      source: "home_input",
      shareLocation,
    });
    // 입력창은 비운다. 🚨 원문은 말풍선이 들고 있다 — 서버가 끝을 말하기 전에는 거기서 안 사라진다.
    if (!sent) return;
    clearDraft(childId);
    // 🚨 지난 날부터 연 화면이면 오늘 대화로 넘어간다 — 여기서는 그날 아래에 오늘 줄을 붙일 수 없다 (머리말).
    //    `replace` 다: 기기 뒤로가기가 이 화면으로 돌아오면 방금 보낸 줄이 안 보이는 화면이 된다.
    if (target) router.replace(`/child/${childId}/chat`);
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
          <header ref={topBarRef} className="-ml-3 flex items-center gap-1">
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
              {/* 🚨 제목에 "오늘" 을 붙이지 않는다 (#541) — 캘린더에서 지난 날을 보러 와도, 위로 올려 전날을
                보고 있어도 같은 제목이다. 어느 날인지는 아래 줄이 말한다. */}
              <PageTitle>대화</PageTitle>
              {/* 누구의 어느 날 대화인지 — 스크롤을 따라 바뀐다 (`shownDay`). 🚨 날짜 머리줄은 날이 둘 이상일
                때만 본문에 선다 (`ChatThread`) — 하루뿐이면 이 줄이 그 말을 한다. */}
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
                savedAlongside={savedAlongside(
                  conversation.turns,
                  history.turns,
                  conversation.answering.runId,
                )}
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
          <MoreTurns
            label="이전 대화 보기"
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
          history.isPending ? (
            <SkeletonBlock label="대화를 불러오는 중" />
          ) : isError ? null : target ? (
            // 그날 대화가 없다 — 지웠거나 주소를 손으로 고친 경우다. 다른 날은 위아래 문으로 이어 볼 수 있다.
            <EmptyState
              icon={MessagesSquare}
              title="이날은 나눈 대화가 없어요"
              description="아래에 한 줄 적으면 오늘 대화로 이어져요."
              count={0}
              countLabel="이날 보낸 한 줄"
            />
          ) : (
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
            turns={liveTurns}
            answering={conversation.answering}
            onOpenDrafts={(key, draftIds) => setDraftsOf({ key, draftIds })}
            onPickOffer={goToSuggestions}
          />
        )}

        {target && hasPreviousPage ? (
          <MoreTurns
            label="다음 대화 보기"
            loading={isFetchingPreviousPage}
            failed={isFetchPreviousPageError}
            onMore={loadNewer}
          />
        ) : null}

        {/* 🚨 **승인 게이트 ㉠ 이 이 시트 안의 카드에 있다.** 시트에는 등장 애니메이션이 없다
          (디자인 시스템 §8). 닫아도 초안은 남는다 — 넣지 않은 장은 다시 열면 그대로 선다. */}
        <BottomSheet
          open={draftsOf !== null}
          onClose={() => setDraftsOf(null)}
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
              key={draftsOf.key}
              childId={childId}
              incoming={[]}
              draftIds={draftsOf.draftIds}
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
 * 더 이른(지난 날부터 연 화면에서는 더 늦은) 대화로 가는 문 (#228). 위아래로 밀면 저절로 붙지만, 화면이 짧아
 * 밀 수 없을 때 이 버튼이 길이다.
 * 🚨 다음 장만 실패한 것은 받아 둔 대화를 지우지 않고 여기서 말한다 (07 "기록 더 보기" 와 같다).
 */
function MoreTurns({
  label,
  loading,
  failed,
  onMore,
}: {
  label: string;
  loading: boolean;
  failed: boolean;
  onMore: () => void;
}) {
  return (
    <div className="flex flex-col items-center gap-2">
      {failed ? (
        <p role="status" className="text-body-sm text-ink-muted">
          대화를 더 불러오지 못했어요. 다시 눌러 주세요.
        </p>
      ) : null}
      <Button variant="tertiary" size="compact" onClick={onMore} disabled={loading}>
        {loading ? "불러오는 중" : label}
      </Button>
    </div>
  );
}

/**
 * 그 질문을 낸 run 이 저장한 것이 있는가 — 입력창 위 문장이 이걸로 갈린다 (`AnsweringBar`).
 * 지난 턴의 질문(다시 켠 뒤 15분 안 · `reply_open`)이면 서버가 남긴 턴에서 본다.
 */
function savedAlongside(turns: ChatTurn[], past: readonly PastTurn[], runId: string): boolean {
  const live = turns.find((t) => t.send.status === "sent" && t.send.runId === runId);
  if (live) return live.run.observations.length > 0;
  const earlier = past.find((t) => t.runId === runId);
  return (earlier?.run.observations.length ?? 0) + (earlier?.deleted.length ?? 0) > 0;
}

/** 지난 날부터 연 화면이 오늘까지 내려오기 전에 넘기는 빈 줄 목록 (렌더마다 새 배열을 만들지 않는다). */
const NO_TURNS: ChatTurn[] = [];
