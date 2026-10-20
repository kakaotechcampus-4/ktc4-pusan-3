"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { CalendarOff, ChevronRight, PenLine, Pencil, Trash2 } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";

import { ObservationList } from "@/components/observation-list";
import { Button, ButtonLink } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { EmptyState } from "@/components/ui/empty-state";
import { ICON_SIZE, ICON_STROKE } from "@/components/ui/icon";
import { IconButton } from "@/components/ui/icon-button";
import { IconTile } from "@/components/ui/icon-tile";
import { Spinner } from "@/components/ui/spinner";
import { TextArea } from "@/components/ui/text-area";
import { useToast } from "@/components/ui/toast";
import { api, qk } from "@/lib/api";
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
 * 09 하루 패널 — 고른 날의 내 일기 · 일정 · 관찰 · 나눈 대화 · 함께 보는 사진 (이 순서로 선다 · #503 · #228).
 *
 * 🚨 **없는 구역은 그리지 않는다.** 빈 상자를 늘어놓아 하루를 채우지 않는다 — 프로토타입의
 *    "기록이 없는 날은 비워둬요. 채우려고 만들지 않아요" 가 이 화면의 태도다. 일기 카드만은
 *    빈 날에도 서지만 "이날의 일기 쓰기" 줄 하나뿐이다 (`DiaryCard`).
 *
 * 🚨 **일기와 관찰을 한 구역에 섞지 않는다.** 일기는 관찰로 자동 추출되지 않는다(계약서 §09) —
 *    사용자가 일기라고 쓴 것을 아이 성향으로 조용히 승격시키면 신뢰가 깨진다. 화면이 그 사실을
 *    말하고, 기억으로 남기는 길(홈의 한 줄)을 따로 알려준다.
 *
 * 🚨 **사진은 보기만 한다** (#503). 이미 남은 사진이 있는 날에만 사진 구역이 선다. 사진을 넣는
 *    길은 홈의 카메라 하나다 — 이 패널에 "사진으로 적기" 를 두지 않는다.
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
   * 일기 편집기가 열려 있다.
   *
   * 🚨 **빈 입력창을 하루마다 세워 두지 않는다.** 처음에는 일기 칸을 늘 펼쳐 뒀는데, 그러면
   *    아무것도 없는 날에 "채우려고 만들지 않아요" 바로 위에 **채우라는 큰 입력 상자**가 서서
   *    화면이 스스로를 부정했다. 쓰겠다고 누른 날만 편집기가 열린다.
   *
   * 날짜가 바뀌면 이 상태도 처음으로 돌아가야 한다 — 그건 이 컴포넌트를 `key={date}` 로
   * 세우는 쪽(09 화면)이 책임진다.
   */
  const [editingDiary, setEditingDiary] = useState(false);
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
      {/* 🚨 **일기가 맨 위다** (#503). 보호자가 그날을 다시 열 때 먼저 찾는 것은 자기가 쓴 글인데,
          기록 아래에 두면 기록이 있는 날마다 스크롤해야 보였다. */}
      <DiaryCard
        childId={childId}
        date={date}
        data={data}
        editing={editingDiary}
        onEditingChange={setEditingDiary}
      />

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

      {/* 그날 나눈 대화로 가는 문 (#228). 🚨 **대화가 없는 날에는 세우지 않는다** — 일기와 달리 여기서
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
          나눈다. 그래서 쓰는 줄은 빈 상태가 아니라 일기 구역에 따로 서고, 쓰는 동안에는
          빈 상태를 내린다. 일기를 지워 그날 다른 것이 없으면 이 빈 상태가 다시 선다. */}
      {nothing && !editingDiary ? (
        <EmptyState
          icon={CalendarOff}
          title="이날은 아무것도 없어요"
          description="기록이 없는 날은 비워둬요. 채우려고 만들지 않아요."
          count={0}
          countLabel="이날의 기록"
        />
      ) : null}

      {data.shared_photos.length > 0 ? (
        <Section title="이날의 사진">
          <Photos photos={data.shared_photos} />
        </Section>
      ) : null}
    </div>
  );
}

function Section({
  title,
  action,
  children,
}: {
  title: string;
  /** 제목 줄 오른쪽 끝의 그 구역 행동. */
  action?: React.ReactNode;
  children?: React.ReactNode;
}) {
  // 제목이 구역의 이름이 된다 — 스크린리더가 구역(region)으로 건너다닐 수 있다.
  const headingId = useId();
  return (
    <section aria-labelledby={headingId} className="flex flex-col gap-2">
      {/* 🚨 **줄이 접힌다** (`flex-wrap`). 글자를 키우면 제목과 버튼이 한 줄에 안 들어가고,
          `keep-all` 이라 단어를 쪼개지도 못해 화면 밖으로 넘친다.
          🚨 **제목 줄은 버튼이 없어도 버튼 높이다** (`min-h-touch`). 버튼(44)이 선 구역만 줄이 커지면
          그 구역만 제목과 내용 사이가 벌어져 머리 모양이 둘이 된다. 버튼을 위아래로 당겨 맞추면
          버튼 아랫변이 바로 아래 카드에 닿는다. */}
      <div className="min-h-touch flex flex-wrap items-center justify-between gap-x-3 gap-y-2">
        <h3 id={headingId} className="text-section text-ink">
          {title}
        </h3>
        {/* 아이콘 버튼은 원(44) 자체가 여백이라 사이를 띄우지 않는다 (11-1 과 같다). */}
        {action ? <div className="flex items-center">{action}</div> : null}
      </div>
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

/**
 * 09 일기 구역 — 쓰기 · 읽기 · 고치기 · 지우기 확인을 **한 구역 안에서** 한다 (#503).
 *
 * 🚨 **누른 자리 바로 아래가 바뀐다.** 한동안 "일기 쓰기" 는 화면 머리에, "일기 고치기" · "일기 지우기" 는
 *    카드 **아래 바깥**에 있었다. 쓰기를 누르면 멀리 떨어진 자리에서 편집기가 열렸고, 고치기 · 지우기는
 *    카드와 따로 노는 버튼 줄로 보였다.
 *    - 일기가 없으면 글 카드가 설 자리에 "이날의 일기 쓰기" 줄이 서고, 누르면 그 줄이 편집기가 된다.
 *    - 일기가 있으면 고치기 · 지우기가 제목 줄 오른쪽 아이콘이다.
 * 🚨 **제목은 다른 구역과 같은 `Section` 머리다.** 제목을 카드 안에 넣었던 때는 한 패널에 머리 모양이 둘이었다.
 * 🚨 **빈 입력창을 세우지 않는다** (아래 `editing`). 빈 날에도 구역은 서지만 누르는 줄 하나뿐이라,
 *    그날 일기를 쓰는 법이 날마다 같다.
 */
function DiaryCard({
  childId,
  date,
  data,
  editing,
  onEditingChange,
}: {
  childId: string;
  date: string;
  data: CalendarDayResponse;
  /**
   * 편집기가 열려 있다. 하루 패널이 든다 — 쓰는 동안은 빈 상태("채우려고 만들지 않아요")를
   * 내려야 해서다. 날짜가 바뀌면 패널째 새로 서서(`key={date}`) 닫힌다.
   */
  editing: boolean;
  onEditingChange: (editing: boolean) => void;
}) {
  const queryClient = useQueryClient();
  const saved = data.my_diary?.content ?? "";
  const hasDiary = data.my_diary !== null;
  const [draft, setDraft] = useState(saved);
  /** "지우기" 를 눌러 확인 패널이 열려 있다 (아래 읽는 상태의 🚨). */
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
   * 🚨 **"그만두기" 로 닫으면 포커스를 "지우기" 로 돌려준다.** 누른 버튼이 패널째 사라져서
   *    그대로 두면 포커스가 `<body>` 로 떨어지고, 키보드 · 스크린리더는 화면 맨 위에서 다시 찾아
   *    내려와야 한다. 패널을 연 버튼이 돌아갈 자리다 (`Select` 가 닫힐 때와 같은 규칙).
   */
  const deleteToggleRef = useRef<HTMLButtonElement>(null);

  /**
   * 🚨 **저장 · 취소 · 지우기 뒤에는 다시 선 "이날의 일기 쓰기" 줄이나 "고치기" 가 포커스를 받는다.**
   *    누른 버튼이 편집기나 확인 패널째 사라져서, 그대로 두면 포커스가 `<body>` 로 떨어진다.
   *    다시 서는 버튼이 마운트될 때 받는다 — 렌더와 상관없는 표시라 상태가 아니라 ref 다.
   */
  const returnFocus = useRef(false);
  const takeFocus = (node: HTMLButtonElement | null) => {
    if (!node || !returnFocus.current) return;
    returnFocus.current = false;
    node.focus();
  };

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
      returnFocus.current = true;
      onEditingChange(false);
      settleDiary(myDiary);
    },
  });

  /**
   * 그날 내 일기 지우기 (`DELETE /children/{cid}/diary/{date}` · #364). 없는 일기를 지워도 204 다.
   *
   * 🚨 **빈 내용 저장은 지우기가 아니다.** 지우는 길은 이것 하나다. 사진은 그대로 남는다 (#115).
   * 🚨 **승인 게이트가 아니다** — 게이트는 캘린더 쓰기와 건강 기록 확정 두 곳뿐이다 (최상위 §2).
   *    성공하면 `my_diary: null` 이 되어 구역이 "이날의 일기 쓰기" 줄 하나로 돌아간다.
   */
  const remove = useMutation({
    mutationFn: () => api.delete<void>(diaryPath),
    onSuccess: () => {
      returnFocus.current = true;
      setConfirmingDelete(false);
      onEditingChange(false);
      settleDiary(null);
    },
  });

  /** 이미 있는 일기를 다 비웠다. 저장이 막히는 이유와 지우는 길을 글자로 말한다. */
  const emptied = saved !== "" && draft.trim() === "";

  /*
    🚨 **일기가 있는 날의 행동은 "이날의 일기" 제목 줄 오른쪽에 있다** (#503). 제목은 다른 구역("저장된 일정" ·
       "이날 남긴 기록" · "이날의 사진")과 같은 `Section` 머리다. 쓰는 동안에는 행동이 없다(편집기가
       저장 · 취소를 갖는다). 일기가 없는 날은 제목 줄에 행동이 없고, 아래의 쓰기 줄이 그 일을 한다.
  */
  const actions = hasDiary ? (
    /* 🚨 **고치기 · 지우기는 아이콘이다** (11-1 측정 기록 줄과 같은 처리). 글자 버튼 둘을 나란히 두면
          제목 줄이 무거워져 일기 글보다 버튼이 먼저 읽히고, 360px 에서는 줄이 두 겹으로 접힌다.
          뜻은 `IconButton` 의 `label`(스크린리더 이름 + 툴팁)이 지고, 누르면 열리는 편집기 · 확인 띠가
          글자로 다시 말한다.
       🚨 **고치기가 왼쪽이다** (11-1 과 같은 순서). 일기에 더 자주 하는 일은 고치는 쪽이고,
          지우기가 먼저 손에 닿으면 고치면 되는 것을 지우게 된다. 무게는 아래 확인 띠가 진다.
       🚨 `Trash2` 가 맞다 — 일기는 정말 없어진다 (11-1 과 같다. 행이 남는 안전 정보 내리기는 `CircleMinus`). */
    <>
      <IconButton
        ref={takeFocus}
        label="일기 고치기"
        onClick={() => {
          setConfirmingDelete(false);
          remove.reset();
          setDraft(saved);
          onEditingChange(true);
        }}
        disabled={remove.isPending}
      >
        <Pencil aria-hidden size={ICON_SIZE.md} strokeWidth={ICON_STROKE} />
      </IconButton>
      <IconButton
        ref={deleteToggleRef}
        label="일기 지우기"
        aria-expanded={confirmingDelete}
        onClick={() => {
          setConfirmingDelete((open) => !open);
          remove.reset();
        }}
        disabled={remove.isPending}
      >
        <Trash2 aria-hidden size={ICON_SIZE.md} strokeWidth={ICON_STROKE} />
      </IconButton>
    </>
  ) : null;

  return (
    <Section title="이날의 일기" action={editing ? null : actions}>
      {editing ? (
        // 편집기 카드는 두 덩어리다 — 입력칸과 그 아래 안내문(붙인다), 저장 · 취소와 그 사유(띄운다).
        <div className="rounded-card bg-surface border-line flex flex-col gap-4 border p-4">
          <div className="flex flex-col gap-1.5">
            <TextArea
              label="이날의 일기"
              labelHidden
              // 🚨 편집기는 누른 뒤에만 선다("이날의 일기 쓰기" · "고치기"). 바로 적을 수 있게 포커스를 옮긴다.
              autoFocus
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              placeholder="이날 어땠는지 편하게 적어주세요."
            />
            <DiaryNote />
          </div>
          <div className="flex flex-col gap-2">
            <div className="flex gap-2">
              <Button
                onClick={() => save.mutate(draft)}
                // 🚨 공백뿐이면 막는다 — 서버가 422 로 거절한다. 다 지우고 싶으면 "지우기" 다 (#364).
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
                  returnFocus.current = true;
                  setDraft(saved);
                  save.reset();
                  onEditingChange(false);
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
                비운 채로는 저장되지 않아요. 일기를 지우려면 취소한 뒤 지우기를 눌러주세요.
              </p>
            ) : null}

            {/* 🚨 실패를 빨강으로 칠하지 않는다 (문서 §3). */}
            {save.isError ? (
              <p className="text-body-sm text-ink-muted" role="status">
                지금은 저장하지 못했어요. 적은 글은 그대로 있어요.
              </p>
            ) : null}
          </div>
        </div>
      ) : !hasDiary ? (
        /*
          🚨 **일기가 없는 날은 누르는 줄 하나다** (#503). 제목 줄 오른쪽 "쓰기" 버튼 하나는 덩그러니
             놓여 무엇을 쓰는지 약했고, 일기가 생기면 그 자리에 글 카드가 서서 자리도 바뀌었다. 이제 글 카드가
             설 자리에 같은 폭의 줄이 서고, 바로 아래 "이날 남긴 기록" 줄과 같은 모양이다(아이콘 · 글 · 화살표).
          🚨 **입력창이 아니다** — 빈 입력 상자를 하루마다 세우지 않는다는 규칙(위 `editingDiary`)은 그대로다.
             누르면 이 줄이 편집기로 바뀐다.
          🚨 아이콘 칸은 `neutral` 이다 — 브랜드색은 정해진 자리에만 쓴다 (디자인 시스템 §3).
        */
        <button
          ref={takeFocus}
          type="button"
          onClick={() => {
            setDraft("");
            onEditingChange(true);
          }}
          className="min-h-touch rounded-field bg-surface border-line active:bg-surface-muted ease-standard flex w-full items-center gap-3 border px-4 py-3.5 text-left transition-colors duration-120"
        >
          <IconTile icon={PenLine} tone="neutral" />
          <span className="text-body text-ink flex-1">이날의 일기 쓰기</span>
          <ChevronRight
            aria-hidden
            size={ICON_SIZE.md}
            strokeWidth={ICON_STROKE}
            className="text-ink-subtle shrink-0"
          />
        </button>
      ) : (
        /*
          🚨 **카드는 칸 둘이 구분선으로 나뉜다** — 글 / 확인 띠(지우기 확인). 확인 띠만 바탕이
             `surface-muted` 다 — 11-1 측정 기록 줄의 확인 띠와 같은 모양이라, 같은 일(한 건 지우기)이
             같은 모양으로 묻는다. 🚨 칸마다 상자를 따로 두르지 않는다 — 카드 안에 카드가 생긴다.
          `overflow-hidden` 은 확인 띠의 바탕이 카드의 둥근 아래 모서리를 넘지 않게 한다.
        */
        <div className="rounded-card bg-surface border-line overflow-hidden border">
          {/* 🚨 `dangerouslySetInnerHTML` 을 쓰지 않는다. 줄바꿈은 CSS 로 살린다. */}
          <p className="text-body text-ink p-4 whitespace-pre-wrap">{saved}</p>
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
            🚨 **카드 안에 카드를 겹치지 않는다** (#503). 확인 패널은 카드 바닥의 띠다 — 테두리 상자가
               아니라 위쪽 구분선과 `surface-muted` 바탕으로 나눈다 (11-1 확인 띠와 같은 값).
            🚨 **버튼 줄은 접힌다** (`flex-wrap`). 글자를 키우거나 좁은 폭이면 "지우기" · "그만두기" 가
               한 줄에 안 들어가고, `keep-all` 이라 단어를 쪼개지도 못해 패널 밖으로 넘친다. 접히면
               각자 한 줄을 다 쓴다 (`flex-1`).
            🚨 **확인 문구는 묻는 한 줄뿐이다** (#503). "되살릴 수 없어요 · 사진은 그대로 남아요" 를
               붙였었는데, 한 편을 지우는 확인치고 글이 길어 띠가 무거웠다 (10-17 결정).
            🚨 **실패하면 패널이 남는다.** 다시 누르는 것이 재시도다 — `DELETE` 는 몇 번 보내도 결과가
               같아서 키가 없다. 실패를 빨강으로 칠하지 않는다 (문서 §3).
          */}
          {!editing && confirmingDelete ? (
            <div
              ref={(node) => {
                confirmRef.current = node;
                revealRef.current = node;
              }}
              // 🚨 `scroll-mb-*` 가 없으면 `scrollIntoView` 가 하단 네비 **뒤**를 "보인다" 고
              //    판단해서 버튼이 그대로 가려진다 (11-1 과 같은 값).
              className="bg-surface-muted border-line flex scroll-mb-28 flex-col gap-3 border-t px-4 py-3.5"
            >
              <p className="text-body text-ink">이 일기를 지울까요?</p>
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
        </div>
      )}
    </Section>
  );
}

/**
 * 🚨 계약서 §09 의 "일기는 관찰로 자동 추출하지 않는다" 를 화면이 말한다. 부모가 무엇을
 *    쓰는지 알고 쓰게 하는 문장이고, 기억으로 남기는 길이 따로 있다는 것도 같이 알려준다.
 *    **쓰는 동안에만** 선다 (#503) — 알고 써야 하는 순간이 그때고, 읽는 카드마다 같은 문장이
 *    붙으면 카드가 일기보다 안내문으로 읽힌다.
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
