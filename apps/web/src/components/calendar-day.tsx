"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { CalendarOff } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useId, useRef, useState } from "react";

import { ObservationList } from "@/components/observation-list";
import { PhotoSourceSheet } from "@/components/photo-source-sheet";
import { Button, ButtonLink } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { EmptyState } from "@/components/ui/empty-state";
import { Spinner } from "@/components/ui/spinner";
import { TextArea } from "@/components/ui/text-area";
import { useToast } from "@/components/ui/toast";
import { usePhotoDraftStore } from "@/stores/photo-draft";
import { api, qk } from "@/lib/api";
import { PENDING_SERVER_FEATURES } from "@/lib/features";
import { createSerialQueue } from "@/lib/serial-queue";
import type {
  CalendarDayResponse,
  CalendarEvent,
  DiaryRequest,
  EventItem,
  EventItemUpdateResponse,
  MyDiary,
  Observation,
  SharedPhoto,
} from "@/lib/api/types";
import { formatEventTime } from "@/lib/format";
import { useReveal } from "@/lib/motion";
import { FORWARD } from "@/lib/view-transition";

/**
 * 09 하루 패널 — 고른 날의 일정 · 관찰 · 내 일기 · 함께 보는 사진.
 *
 * 🚨 **없는 구역은 그리지 않는다.** 빈 상자를 늘어놓아 하루를 채우지 않는다 — 프로토타입의
 *    "기록이 없는 날은 비워둬요. 채우려고 만들지 않아요" 가 이 화면의 태도다.
 *
 * 🚨 **일기와 관찰을 한 구역에 섞지 않는다.** 일기는 관찰로 자동 추출되지 않는다(계약서 §09) —
 *    사용자가 일기라고 쓴 것을 아이 성향으로 조용히 승격시키면 신뢰가 깨진다. 화면이 그 사실을
 *    말하고, 기억으로 남기는 길(홈의 한 줄)을 따로 알려준다.
 *
 * 🚨 **사진을 여기서 올리지 않는다.** 넣는 것은 08 사진 화면(`/child/{cid}/photos?date=`)이고,
 *    이 패널은 그 화면으로 가는 문 하나와 이미 남은 사진의 조회만 맡는다 — 사진은 읽어낸 것을
 *    보호자가 확인해야 저장되는데(승인 전 저장 금지) 그 확인이 하루 패널에 들어갈 크기가 아니다.
 *
 * 🚨 **일기와 사진은 다른 저장소다** (#115). 일기(`my_diary`)는 쓴 보호자 본인 것만 오고, 사진
 *    (`shared_photos`)은 연결된 보호자가 함께 본다. 그래서 두 구역이 따로 서고, 사진만 있는 날도 있다.
 */
export function CalendarDayPanel({
  childId,
  date,
  data,
  onOpenObservation,
}: {
  childId: string;
  /** `YYYY-MM-DD`. */
  date: string;
  data: CalendarDayResponse;
  onOpenObservation: (observation: Observation) => void;
}) {
  /**
   * 🚨 **빈 입력창을 하루마다 세워 두지 않는다.** 처음에는 일기 칸을 늘 펼쳐 뒀는데, 그러면
   *    아무것도 없는 날에 "채우려고 만들지 않아요" 바로 위에 **채우라는 큰 입력 상자**가 서서
   *    화면이 스스로를 부정했다. 쓰겠다고 누른 날만 편집기가 열린다.
   *
   * 날짜가 바뀌면 이 상태도 처음으로 돌아가야 한다 — 그건 이 컴포넌트를 `key={date}` 로
   * 세우는 쪽(09 화면)이 책임진다.
   */
  const [writing, setWriting] = useState(false);
  /**
   * 방금 일기를 지웠다. 🚨 지우면 누른 버튼이 구역째 사라져서 포커스가 `<body>` 로 떨어진다 —
   * 키보드 · 스크린리더 사용자는 어디에 서 있는지 잃는다. 그 자리에 새로 서는 "일기 쓰기" 가
   * 포커스를 받는다 (프로그램 포커스라 터치로 눌렀을 때는 테두리가 보이지 않는다).
   */
  const [diaryDeleted, setDiaryDeleted] = useState(false);
  const [photoSheetOpen, setPhotoSheetOpen] = useState(false);
  const putPhoto = usePhotoDraftStore((s) => s.putPhoto);
  const router = useRouter();

  const hasDiary = data.my_diary !== null;
  // 🚨 사진도 센다. 사진은 일기 밖에 있어서, 빼면 사진만 있는 날에 "아무것도 없어요" 가
  //    그 사진 바로 위에 선다.
  const nothing =
    data.events.length === 0 &&
    data.observations.length === 0 &&
    !hasDiary &&
    data.shared_photos.length === 0 &&
    data.chat_count === 0;

  return (
    <div className="flex flex-col gap-5">
      {data.events.length > 0 ? (
        <Section title="저장된 일정">
          <ul className="flex flex-col gap-3">
            {data.events.map((event) => (
              <li key={event.id}>
                <EventCard childId={childId} date={date} event={event} />
              </li>
            ))}
          </ul>
        </Section>
      ) : null}

      {data.observations.length > 0 ? (
        <Section title="이날 남긴 기록">
          <ObservationList observations={data.observations} onOpen={onOpenObservation} />
        </Section>
      ) : null}

      {/* 그날 나눈 대화로 가는 문 (#228). 🚨 **대화가 없는 날에는 세우지 않는다** — 일기 · 사진과 달리 여기서
          대화를 시작하지 않아서(대화는 홈의 한 줄로 시작한다) 빈 날에 세우면 아무것도 없는 곳으로 가는 문이 된다.
          🚨 내 대화만 센 수다 — 같은 아이의 다른 보호자 대화는 보이지 않는다. */}
      {data.chat_count > 0 ? (
        <Section title="이날 나눈 대화">
          <div>
            <ButtonLink
              href={`/child/${childId}/chat?date=${date}`}
              variant="tertiary"
              size="compact"
              transitionTypes={FORWARD.transitionTypes}
            >
              대화 {data.chat_count}개 보기
            </ButtonLink>
          </div>
        </Section>
      ) : null}

      {/* 🚨 **"채우려고 만들지 않아요" 와 채우는 행동을 붙여 놓지 않는다.** 빈 상태에 `일기 쓰기`
          버튼을 달았더니 화면이 스스로를 부정했다 — 사실을 말하는 자리와 할 일을 주는 자리를
          나눈다. 빈 날이라고 일기 구역을 숨기지도 않는다. 그러면 "그날 일기를 쓰는 법" 이
          날마다 달라져서, 빈 날에만 다른 버튼을 찾아야 한다. */}
      {nothing && !writing ? (
        <EmptyState
          icon={CalendarOff}
          title="이날은 아무것도 없어요"
          description="기록이 없는 날은 비워둬요. 채우려고 만들지 않아요."
          count={0}
          countLabel="이날의 기록"
        />
      ) : null}

      {hasDiary || writing ? (
        <Section title="이날의 일기">
          <Diary
            childId={childId}
            date={date}
            data={data}
            onDone={() => setWriting(false)}
            onDeleted={() => setDiaryDeleted(true)}
          />
        </Section>
      ) : (
        // 일기만 없는 날. 구역 제목은 남기되 입력창은 접어 둔다 — 빈 날도 같은 모양이다.
        // 일기를 지운 뒤에도 이 모양으로 돌아온다 — 그날 다른 것이 없으면 위의 빈 상태도 다시 선다.
        <Section title="이날의 일기">
          <div>
            <Button
              variant="tertiary"
              size="compact"
              // 지운 직후에만 건다 (위 `diaryDeleted`). 날을 처음 열 때 포커스를 뺏지 않는다.
              autoFocus={diaryDeleted}
              onClick={() => {
                setDiaryDeleted(false);
                setWriting(true);
              }}
            >
              일기 쓰기
            </Button>
          </div>
          <DiaryNote />
        </Section>
      )}

      {/* 🚨 일기 구역과 같은 규칙이다 — **빈 날에도 같은 모양으로** 선다. 사진이 있는 날에만
          이 구역이 생기면 "그날 사진을 넣는 법" 이 날마다 달라져서, 빈 날에는 부모가 홈까지
          올라가 카메라 버튼을 찾아야 한다.
          ⚠️ 넣는 길이 닫혀 있는 동안(`lib/features.ts` · #409)은 이미 남은 사진이 있는 날에만 선다 —
          넣을 수 없는 날에 빈 구역만 세우면 아무 데도 안 가는 자리가 된다. */}
      {PENDING_SERVER_FEATURES.photoEntry ? (
        <Section title="이날의 사진">
          {data.shared_photos.length > 0 ? <Photos photos={data.shared_photos} /> : null}
          <div>
            <Button variant="tertiary" size="compact" onClick={() => setPhotoSheetOpen(true)}>
              사진으로 적기
            </Button>
          </div>
          <p className="text-caption text-ink-subtle">
            사진에서 읽어낸 것을 보여드리고, 승인해야 저장돼요.
          </p>
          {/* 🚨 홈과 **같은 시트**다. 사진을 고르는 방법이 화면마다 다르면 부모가 매번 다시 찾는다. */}
          <PhotoSourceSheet
            open={photoSheetOpen}
            onClose={() => setPhotoSheetOpen(false)}
            onPick={({ file, lane }) => {
              putPhoto(childId, file, lane);
              setPhotoSheetOpen(false);
              // 🚨 고른 날을 그대로 싣는다 — 08 이 날짜를 다시 계산하지 않는다.
              router.push(`/child/${childId}/photos?date=${date}`, FORWARD);
            }}
          />
        </Section>
      ) : data.shared_photos.length > 0 ? (
        <Section title="이날의 사진">
          <Photos photos={data.shared_photos} />
        </Section>
      ) : null}
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="flex flex-col gap-2">
      <h3 className="text-section text-ink">{title}</h3>
      {children}
    </section>
  );
}

/* ── 일정 ─────────────────────────────────────────────────────────────── */

/**
 * 🚨 여기 서 있는 일정은 전부 **보호자가 이미 제출한 것**이다. `event` 에는 제출한 행만 들어가고
 *    초안은 행이 아니라 이 화면에 나오지 않는다 (#118) — 승인 게이트 ㉠ 은 05·06 이 진다. 이 화면에
 *    `btn-approve` 나 `caution` 이 없는 이유가 그것이다.
 */
function EventCard({
  childId,
  date,
  event,
}: {
  childId: string;
  date: string;
  event: CalendarEvent;
}) {
  return (
    <div className="rounded-card bg-surface border-line border p-4">
      <p className="text-body text-ink">{event.title}</p>
      <p className="text-caption text-ink-subtle mt-1">
        {formatEventTime(event.starts_at, event.all_day)}
      </p>

      {event.items.length > 0 ? (
        <div className="mt-3">
          <p className="text-label text-ink-muted">준비물</p>
          <ul className="mt-1">
            {event.items.map((item) => (
              <li key={item.item_id}>
                <PreparedCheckbox childId={childId} date={date} item={item} />
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}

/**
 * 준비물 체크.
 *
 * 🚨 **낙관적 업데이트를 여기서는 써도 된다.** 금지되는 것은 승인 게이트고(응답 전에 확정된
 *    것처럼 보이면 "되돌릴 수 없는 것은 사람이 승인한다" 가 시각적으로 깨진다 ·
 *    apps/web/CLAUDE.md §3), 준비물 체크는 다시 누르면 그만이다. 체크할 때마다 왕복을
 *    기다리면 가방 싸면서 쓰는 화면이 안 된다.
 */
function PreparedCheckbox({
  childId,
  date,
  item,
}: {
  childId: string;
  date: string;
  item: EventItem;
}) {
  const queryClient = useQueryClient();
  const toast = useToast();
  const key = qk.calendarDay(childId, date);

  /**
   * 🚨 이 준비물의 요청을 한 줄로 세운다. 낙관적 업데이트라 체크박스는 응답을 기다리지 않는데,
   *    체크 → 해제를 빠르게 누르면 `true` · `false` 가 동시에 나가고 응답이 역전되면
   *    **마지막 선택이 아닌 값이 서버에 남는다** (PR #71 리뷰). 체크박스를 잠그는 대신 줄을
   *    세우는 이유는 위 주석과 같다 — 왕복을 기다리는 체크박스로는 가방을 못 싼다.
   *
   *    항목마다 하나씩이라 다른 준비물은 서로 기다리지 않는다 (`serial-queue.ts`).
   */
  const [queue] = useState(createSerialQueue);

  const toggle = useMutation({
    mutationFn: (isPrepared: boolean) =>
      queue.run(() =>
        api.patch<EventItemUpdateResponse>(`/children/${childId}/event-items/${item.item_id}`, {
          is_prepared: isPrepared,
        }),
      ),
    onMutate: async (isPrepared) => {
      await queryClient.cancelQueries({ queryKey: key });
      const previous = queryClient.getQueryData<CalendarDayResponse>(key);
      queryClient.setQueryData<CalendarDayResponse>(key, (current) =>
        current
          ? {
              ...current,
              events: current.events.map((event) => ({
                ...event,
                items: event.items.map((each) =>
                  each.item_id === item.item_id ? { ...each, is_prepared: isPrepared } : each,
                ),
              })),
            }
          : current,
      );
      return { previous };
    },
    // 실패하면 되돌린다 — 체크한 채로 남으면 안 챙긴 것을 챙겼다고 믿게 된다.
    // 🚨 **되돌리기만 하면 조용하다.** 화면에는 체크가 풀린 것만 보이고 왜 그런지가 어디에도
    //    없어서, 부모는 자기가 잘못 눌렀다고 생각한다. 체크박스 옆에는 문장이 들어갈 자리가
    //    없으므로 토스트가 그 자리를 받는다 (`ui/toast.tsx` 의 🚨 — 토스트가 맞는 유일한 자리다).
    onError: (_error, _variables, context) => {
      // 🚨 뒤에 기다리는 선택이 있으면 되돌리지 않는다. 되돌리면 방금 누른 값이 화면에서
      //    한 번 뒤집혔다가 그 요청이 끝나고 다시 바뀐다 — 부모 눈에는 체크가 혼자 춤춘다.
      if (queue.pending === 0 && context?.previous) queryClient.setQueryData(key, context.previous);
      toast.show("준비물 체크를 저장하지 못했어요. 잠시 뒤에 다시 눌러주세요.");
    },
    onSettled: () => {
      // 🚨 줄의 **마지막** 요청만 재조회한다. 중간에 부르면 아직 뒤 요청이 반영되지 않은
      //    서버 값을 받아, 방금 누른 선택을 화면에서 되돌린다.
      if (queue.pending === 0) void queryClient.invalidateQueries({ queryKey: key });
    },
  });

  return (
    <Checkbox
      checked={item.is_prepared}
      onChange={(checked) => toggle.mutate(checked)}
      label={item.item_name}
    />
  );
}

/* ── 일기 ─────────────────────────────────────────────────────────────── */

function Diary({
  childId,
  date,
  data,
  onDone,
  onDeleted,
}: {
  childId: string;
  date: string;
  data: CalendarDayResponse;
  /** 편집이 끝났다(저장했거나 취소했다). 부모가 "일기 쓰기" 상태를 되돌린다. */
  onDone: () => void;
  /** 서버가 지웠다(204). 부모가 그 자리에 서는 "일기 쓰기" 로 포커스를 옮긴다. */
  onDeleted: () => void;
}) {
  const queryClient = useQueryClient();
  const saved = data.my_diary?.content ?? "";
  const [draft, setDraft] = useState(saved);
  const [editing, setEditing] = useState(saved === "");
  /** "일기 지우기" 를 눌러 확인 패널이 열려 있다 (아래 읽는 상태의 🚨). */
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const emptiedHintId = useId();

  /**
   * 🚨 **확인 패널을 화면 안으로 끌어온다.** 09 는 하단 네비가 있는 화면이라 긴 일기 아래에서
   *    패널이 네비 뒤에 열리면 부모 눈에는 아무 일도 안 일어난 것이 된다 (11-1 측정 기록과 같은 처리).
   */
  const confirmRef = useRef<HTMLDivElement>(null);
  const revealRef = useReveal(confirmingDelete);
  useEffect(() => {
    if (confirmingDelete) confirmRef.current?.scrollIntoView({ block: "nearest" });
  }, [confirmingDelete]);

  /**
   * 🚨 **"그만두기" 로 닫으면 포커스를 "일기 지우기" 로 돌려준다.** 누른 버튼이 패널째 사라져서
   *    그대로 두면 포커스가 `<body>` 로 떨어지고, 키보드 · 스크린리더는 화면 맨 위에서 다시 찾아
   *    내려와야 한다. 패널을 연 버튼이 돌아갈 자리다 (`Select` 가 닫힐 때와 같은 규칙).
   */
  const deleteToggleRef = useRef<HTMLButtonElement>(null);

  const dayKey = qk.calendarDay(childId, date);
  const diaryPath = `/children/${childId}/diary/${date}`;

  /**
   * 응답(또는 지운 사실)을 하루 보기 캐시에 바로 얹고, 달 그리드의 일기 표식(`has_my_diary`)만 다시 받는다.
   * 무효화는 `qk.calendarMonths` 접두어 — 이 아이의 **달 보기 쿼리만** 걸린다 (하루 보기는 방금 얹었으니
   * 다시 받지 않는다). 일기는 달 보기 말고 읽는 화면이 없다 (홈 응답에 일기가 없다).
   */
  const settleDiary = (myDiary: MyDiary | null) => {
    queryClient.setQueryData<CalendarDayResponse>(dayKey, (current) =>
      current ? { ...current, my_diary: myDiary } : current,
    );
    void queryClient.invalidateQueries({ queryKey: qk.calendarMonths(childId) });
  };

  /**
   * 그날 내 일기 쓰기 (`PUT /children/{cid}/diary/{date}` · #364). 같은 날 다시 쓰면 덮어쓴다.
   *
   * 🚨 **본문은 고쳐 보내지 않는다** — 앞뒤 공백 · 줄바꿈까지 서버가 원문 그대로 저장한다.
   *    공백뿐인 내용은 서버가 422 로 거절하므로 버튼이 먼저 막는다 (아래 `disabled`).
   * 🚨 **"저장됐다" 는 응답으로만 만든다.** 응답이 하루 보기 `my_diary` 와 같은 모양이라 그대로 얹는다 —
   *    `onMutate` 로 먼저 바꾸지 않는다. 사진은 일기 밖이라 싣지 않는다 (#115).
   * Idempotency-Key 를 받지 않는 경로다 — 되돌릴 수 없는 5곳이 아니다.
   */
  const save = useMutation({
    mutationFn: (content: string) => {
      const body: DiaryRequest = { content };
      return api.put<MyDiary>(diaryPath, body);
    },
    onSuccess: (myDiary) => {
      setEditing(false);
      onDone();
      settleDiary(myDiary);
    },
  });

  /**
   * 그날 내 일기 지우기 (`DELETE /children/{cid}/diary/{date}` · #364). 없는 일기를 지워도 204 다.
   *
   * 🚨 **빈 내용 저장은 지우기가 아니다.** 지우는 길은 이것 하나다. 사진은 그대로 남는다 (#115).
   * 🚨 **승인 게이트가 아니다** — 게이트는 캘린더 쓰기와 건강 기록 확정 두 곳뿐이다 (최상위 §2).
   *    성공하면 `my_diary: null` 이 되어 부모가 이 구역을 "일기 쓰기" 상태로 되돌린다.
   */
  const remove = useMutation({
    mutationFn: () => api.delete<void>(diaryPath),
    onSuccess: () => {
      setEditing(false);
      onDone();
      onDeleted();
      settleDiary(null);
    },
  });

  if (!editing) {
    return (
      <div className="flex flex-col gap-2">
        <div className="rounded-card bg-surface border-line border p-4">
          {/* 🚨 `dangerouslySetInnerHTML` 을 쓰지 않는다. 줄바꿈은 CSS 로 살린다. */}
          <p className="text-body text-ink whitespace-pre-wrap">{saved}</p>
        </div>
        {/* 🚨 **고치기가 왼쪽이다** (11-1 측정 기록 줄과 같은 순서). 일기에 더 자주 하는 일은
            고치는 쪽이고, 지우기가 먼저 손에 닿으면 고치면 되는 것을 지우게 된다.
            둘 다 `tertiary` · `compact` 다 — 카드 아래의 행동이고, 지우기를 고치기보다 무겁게
            칠할 이유는 없다. 무게는 아래 확인 패널이 진다. */}
        <div className="flex gap-2">
          <Button
            variant="tertiary"
            size="compact"
            onClick={() => {
              setConfirmingDelete(false);
              remove.reset();
              setEditing(true);
            }}
            disabled={remove.isPending}
          >
            일기 고치기
          </Button>
          <Button
            ref={deleteToggleRef}
            variant="tertiary"
            size="compact"
            aria-expanded={confirmingDelete}
            onClick={() => {
              setConfirmingDelete((open) => !open);
              remove.reset();
            }}
            disabled={remove.isPending}
          >
            일기 지우기
          </Button>
        </div>

        {/*
          🚨 **지우기 전에 한 번 묻는다. 하지만 승인 게이트가 아니다.**
             일기는 보호자가 손으로 쓴 글이고 화면에 지운 것을 되살리는 길이 없다 — 잘못 누른 한 번에
             글이 통째로 사라지면 다시 쓸 수 없는 글이다. 그래서 묻는다. 게이트는 캘린더 쓰기와
             건강 기록 확정 **딱 2곳**이고(최상위 §2), 그 둘은 AI 가 준비한 것을 사람이 확정하는
             자리다. 여기는 보호자가 자기 글을 스스로 지우는 자리라 같은 종류가 아니다 — 그래서
             `btn-approve` 도 `caution` 도 `Idempotency-Key` 도 없다 (11-1 측정 기록 지우기 ·
             07 교정의 확인 단계와 같은 칸이다).
          🚨 **확정은 `btn-secondary` 다 — `btn-danger` 도 `btn-primary` 도 아니다.** 결정과 이유는
             디자인 시스템 §11 표 "09 캘린더 · 일기 지우기 확인" 과 §7 버튼에 있다. 짧게:
             빨강은 지우면 그 한 건 밖의 것이 같이 바뀌는 확정(계정 · 보호자 연결 · 안전 정보)에
             남긴다 — 일기 한 편은 거기서 끝나고 같은 날 다시 쓸 수 있다. 읽는 상태에 화면 primary
             가 없어 초록을 쓸 수는 있지만, 채운 초록이 "없애는 행동" 을 가리키게 된다. 11-1 과 같다.
          🚨 **버튼 줄은 접힌다** (`flex-wrap`). 글자를 키우거나 좁은 폭이면 "지우기" · "그만두기" 가
             한 줄에 안 들어가고, `keep-all` 이라 단어를 쪼개지도 못해 패널 밖으로 넘친다. 접히면
             각자 한 줄을 다 쓴다 (`flex-1`).
          🚨 **확인 문구는 일어날 일만 말한다** (디자인 시스템 §7 "교정"). 되살릴 수 없다는 것과,
             같은 날 보이는 사진은 일기 밖이라 그대로라는 것 (#115).
          🚨 **실패하면 패널이 남는다.** 다시 누르는 것이 재시도다 — `DELETE` 는 몇 번 보내도 결과가
             같아서 키가 없다. 실패를 빨강으로 칠하지 않는다 (문서 §3).
        */}
        {confirmingDelete ? (
          <div
            ref={(node) => {
              confirmRef.current = node;
              revealRef.current = node;
            }}
            // 🚨 `scroll-mb-*` 가 없으면 `scrollIntoView` 가 하단 네비 **뒤**를 "보인다" 고
            //    판단해서 버튼이 그대로 가려진다 (11-1 과 같은 값).
            className="border-line rounded-card bg-surface-muted flex scroll-mb-28 flex-col gap-3 border p-4"
          >
            <div>
              <p className="text-body text-ink">이 일기를 지울까요?</p>
              <p className="text-body-sm text-ink-muted mt-1">
                지운 일기는 되살릴 수 없어요. 이날의 사진은 그대로 남아요.
              </p>
            </div>
            <div className="flex flex-wrap gap-2">
              <Button
                variant="secondary"
                size="compact"
                className="flex-1"
                onClick={() => remove.mutate()}
                disabled={remove.isPending}
              >
                {remove.isPending ? <Spinner /> : null}
                {remove.isPending ? "지우는 중이에요" : "지우기"}
              </Button>
              <Button
                variant="tertiary"
                size="compact"
                className="flex-1"
                onClick={() => {
                  setConfirmingDelete(false);
                  remove.reset();
                  deleteToggleRef.current?.focus();
                }}
                disabled={remove.isPending}
              >
                그만두기
              </Button>
            </div>
            {remove.isError ? (
              <p className="text-body-sm text-ink-muted" role="status">
                지금은 지우지 못했어요. 일기는 그대로 있어요.
              </p>
            ) : null}
          </div>
        ) : null}
        <DiaryNote />
      </div>
    );
  }

  /** 이미 있는 일기를 다 비웠다. 저장이 막히는 이유와 지우는 길을 글자로 말한다. */
  const emptied = saved !== "" && draft.trim() === "";

  return (
    <div className="flex flex-col gap-2">
      <TextArea
        label="이날의 일기"
        labelHidden
        value={draft}
        onChange={(event) => setDraft(event.target.value)}
        placeholder="이날 어땠는지 편하게 적어주세요."
      />
      <div className="flex gap-2">
        <Button
          onClick={() => save.mutate(draft)}
          // 🚨 공백뿐이면 막는다 — 서버가 422 로 거절한다. 다 지우고 싶으면 "일기 지우기" 다 (#364).
          disabled={save.isPending || draft.trim() === "" || draft.trim() === saved.trim()}
          aria-describedby={emptied ? emptiedHintId : undefined}
        >
          {save.isPending ? <Spinner /> : null}
          일기 저장
        </Button>
        {/* 🚨 새로 쓰는 중에도 빠져나갈 길을 둔다 — 없으면 잘못 누른 사람이 갇힌다. */}
        <Button
          variant="secondary"
          onClick={() => {
            setDraft(saved);
            setEditing(false);
            onDone();
          }}
        >
          취소
        </Button>
      </div>

      {/* 🚨 **이유 없이 꺼진 버튼을 두지 않는다** — "왜 안 눌리지" 를 먼저 묻게 만든다
          (디자인 시스템 §7 바텀시트). 다 비우고 저장하려던 보호자는 지우고 싶은 것이다 — 빈
          저장은 서버가 지우기로 보지 않으므로(422) 지우는 길을 알려준다.
          🚨 **편집 중에는 지우기를 세우지 않는다.** 아직 저장 안 한 글이 열린 자리에
          "지우기" 가 "취소" 옆에 서면, 고치던 글을 버리는 것인지 저장된 일기를 지우는 것인지
          갈리지 않는다. 새로 쓰는 중이면 지울 것도 없다. 지우는 길은 읽는 상태의 하나뿐이다. */}
      {emptied ? (
        <p id={emptiedHintId} className="text-caption text-ink-subtle">
          비운 채로는 저장되지 않아요. 일기를 지우려면 취소한 뒤 일기 지우기를 눌러주세요.
        </p>
      ) : null}

      {/* 🚨 실패를 빨강으로 칠하지 않는다 (문서 §3). */}
      {save.isError ? (
        <p className="text-body-sm text-ink-muted" role="status">
          지금은 저장하지 못했어요. 적은 글은 그대로 있어요.
        </p>
      ) : null}

      <DiaryNote />
    </div>
  );
}

/**
 * 🚨 계약서 §09 의 "일기는 관찰로 자동 추출하지 않는다" 를 화면이 말한다. 부모가 무엇을
 *    쓰는지 알고 쓰게 하는 문장이고, 기억으로 남기는 길이 따로 있다는 것도 같이 알려준다.
 */
function DiaryNote() {
  return (
    <p className="text-caption text-ink-subtle">
      일기는 아이 기록으로 저장되지 않아요. 기록으로 남기려면 홈에서 한 줄로 적어주세요.
    </p>
  );
}

/* ── 사진 ─────────────────────────────────────────────────────────────── */

function Photos({ photos }: { photos: SharedPhoto[] }) {
  return (
    <ul className="grid grid-cols-2 gap-2">
      {photos.map((photo) => (
        <li key={photo.id} className="rounded-card bg-surface-muted overflow-hidden">
          {/*
            next/image 를 쓰지 않는다 — 사진 주소는 서버가 주는 임의 호스트라 `remotePatterns` 에
            미리 적을 수 없고, 배포마다 달라지면 이미지가 조용히 404 가 된다.
            🚨 `alt` 를 **비워 두지 않는다.** 빈 `alt` 는 "장식이라 안 읽어도 된다" 는 뜻인데
               이건 내용이다. 사진 안에 무엇이 있는지는 우리가 모르므로 **무엇의 사진인지**만
               말한다 — 없는 설명을 지어내는 것과 통째로 숨기는 것 사이의 정직한 자리다.
          */}
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src={photo.image_url}
            alt="이날 캘린더에 남은 사진"
            className="aspect-[4/3] w-full object-cover"
            loading="lazy"
          />
        </li>
      ))}
    </ul>
  );
}
