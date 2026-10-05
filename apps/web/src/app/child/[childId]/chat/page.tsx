"use client";

import { ArrowLeft, MessagesSquare } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { AuthGate } from "@/components/auth-gate";
import { ChatThread } from "@/components/chat-thread";
import { EventDraftList } from "@/components/event-draft-list";
import { HomeComposer } from "@/components/home-composer";
import { PhotoSourceSheet } from "@/components/photo-source-sheet";
import { BottomSheet } from "@/components/ui/bottom-sheet";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { ICON_SIZE, ICON_STROKE } from "@/components/ui/icon";
import { IconButtonLink } from "@/components/ui/icon-button";
import { PageTitle } from "@/components/ui/page-title";
import { Screen } from "@/components/ui/screen";
import { useChildId } from "@/hooks/use-child-id";
import { api, qk, type Agent, type Me } from "@/lib/api";
import { formatDay, toSeoulDateKey } from "@/lib/format";
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
 */
export default function ChatPage() {
  return (
    <AuthGate>
      <ChatScreen />
    </AuthGate>
  );
}

function ChatScreen() {
  const childId = useChildId();
  const router = useRouter();
  const conversation = useConversation(childId);
  const busy = isBusy(conversation);

  // 🚨 이름은 스토어에 캐시하지 않는다. 개인정보는 화면이 필요할 때 Query 로 가져온다 (§3).
  const me = useQuery({ queryKey: qk.me(), queryFn: () => api.get<Me>("/me") });
  const nickname = me.data?.children.find((c) => c.child_id === childId)?.nickname;
  /**
   * 머리에 세우는 날. 🚨 **마지막 한 줄의 날이다** — 앱을 켜 둔 채 자정을 넘기면 "오늘" 과 화면에 있는
   * 대화의 날이 다르다. 대화가 없을 때만 오늘(한국 시간)이다.
   */
  const [today] = useState(() => toSeoulDateKey(new Date()));
  const shownDay = conversation.turns.at(-1)?.day ?? today;

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
  useEffect(() => {
    window.scrollTo({ top: document.documentElement.scrollHeight, behavior: "instant" });
  }, [conversation.turns.length, last?.send.status, last?.run.status]);

  function send() {
    const sent = sendLine(childId, text, {
      // 🚨 입력창 위에 질문이 떠 있을 때만 답이다 (`AnsweringBar`).
      asReply: conversation.answering !== null,
      source: "home_input",
    });
    // 입력창은 비운다. 🚨 원문은 말풍선이 들고 있다 — 서버가 끝을 말하기 전에는 거기서 안 사라진다.
    if (sent) clearDraft(childId);
  }

  function goToSuggestions(agents: Agent[], runId: string) {
    const params = new URLSearchParams({ agents: agents.join(","), run: runId });
    router.push(`/child/${childId}/suggestions?${params.toString()}`);
  }

  return (
    <Screen
      className="gap-6"
      topBar={
        // 🚨 **머리를 붙여 둔다.** 대화는 아래로 자라서, 돌아가는 화살표가 본문에 있으면 몇 번만 주고받아도
        //    화면 밖으로 밀린다 — 흐름 화면이라 네비가 없어서 나가는 길이 그것 하나다.
        // 🚨 줄 전체를 `-ml-3` 로 민다 (44px 원 안의 아이콘이 왼쪽 기준선에 맞게 · 10 › 고객센터와 같다).
        <header className="-ml-3 flex items-center gap-1">
          <IconButtonLink href={`/child/${childId}/home`} label="홈으로 돌아가기">
            <ArrowLeft aria-hidden size={ICON_SIZE.md} strokeWidth={ICON_STROKE} />
          </IconButtonLink>
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
            <AnsweringBar question={conversation.answering} onStop={() => stopAnswering(childId)} />
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
      {conversation.turns.length === 0 ? (
        // 🚨 지난 대화가 남지 않는다는 것을 숨기지 않는다 — 앱을 끄면 사라진다 (#228 전까지).
        <EmptyState
          icon={MessagesSquare}
          title="아래에 한 줄 적으면 여기서 이어져요"
          description="이 대화는 앱을 닫으면 사라져요. 적어주신 기록은 기록 화면에 그대로 남아요."
          count={0}
          countLabel="오늘 보낸 한 줄"
        />
      ) : (
        <ChatThread
          childId={childId}
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
        // 🚨 설명을 달지 않는다 — 목록이 자기 머리글("일정으로 만들까요?" · 넣기 전에는 안 들어간다)을
        //    이미 세운다. 시트 머리에도 같은 말을 두면 한 화면에 같은 두 줄이 겹쳐 선다.
        title="찾은 일정"
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
          router.push(`/child/${childId}/photos`);
        }}
      />
    </Screen>
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
function AnsweringBar({ question, onStop }: { question: ChatQuestion; onStop: () => void }) {
  return (
    <Card>
      <p className="text-caption text-ink-subtle">위 질문에 답하는 중</p>
      {/* 🚨 LLM 이 만든 문장이라 HTML 로 그리지 않는다 (apps/web/CLAUDE.md §4). */}
      <p className="text-body-sm text-ink mt-1">{question.text}</p>
      <p className="text-caption text-ink-subtle mt-2">
        이 질문에 대한 답만 적어주세요. 앞서 적어주신 말은 저장돼 있어요.
      </p>
      <Button variant="tertiary" size="compact" className="mt-3" onClick={onStop}>
        답하지 않고 새로 적기
      </Button>
    </Card>
  );
}
