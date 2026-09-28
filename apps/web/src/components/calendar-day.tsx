"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { CalendarOff } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { ObservationList } from "@/components/observation-list";
import { PhotoSourceSheet } from "@/components/photo-source-sheet";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { EmptyState } from "@/components/ui/empty-state";
import { Spinner } from "@/components/ui/spinner";
import { TextArea } from "@/components/ui/text-area";
import { useToast } from "@/components/ui/toast";
import { usePhotoDraftStore } from "@/stores/photo-draft";
import { api, qk } from "@/lib/api";
import { createSerialQueue } from "@/lib/serial-queue";
import type {
  CalendarDayResponse,
  CalendarDayUpdate,
  CalendarEvent,
  EventItem,
  EventItemUpdateResponse,
  Observation,
} from "@/lib/api/types";
import { formatEventTime } from "@/lib/format";

/**
 * 09 하루 패널 — 고른 날의 일정 · 관찰 · 일기 · 사진.
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
  const [photoSheetOpen, setPhotoSheetOpen] = useState(false);
  const putPhoto = usePhotoDraftStore((s) => s.putPhoto);
  const router = useRouter();

  const hasDiary = data.diary !== null && data.diary.text.trim() !== "";
  const nothing = data.events.length === 0 && data.observations.length === 0 && !hasDiary;

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
          <Diary childId={childId} date={date} data={data} onDone={() => setWriting(false)} />
        </Section>
      ) : (
        // 일기만 없는 날. 구역 제목은 남기되 입력창은 접어 둔다 — 빈 날도 같은 모양이다.
        <Section title="이날의 일기">
          <div>
            <Button variant="tertiary" size="compact" onClick={() => setWriting(true)}>
              일기 쓰기
            </Button>
          </div>
          <DiaryNote />
        </Section>
      )}

      {/* 🚨 일기 구역과 같은 규칙이다 — **빈 날에도 같은 모양으로** 선다. 사진이 있는 날에만
          이 구역이 생기면 "그날 사진을 넣는 법" 이 날마다 달라져서, 빈 날에는 부모가 홈까지
          올라가 카메라 버튼을 찾아야 한다. */}
      <Section title="이날의 사진">
        {data.diary && data.diary.image_urls.length > 0 ? (
          <Photos urls={data.diary.image_urls} />
        ) : null}
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
            router.push(`/child/${childId}/photos?date=${date}`);
          }}
        />
      </Section>
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
 * 🚨 여기 서 있는 일정은 전부 **이미 승인된 것**이다 (`confirmed`). draft 는 캘린더에 쓰이지
 *    않았으므로 이 화면에 나오지 않는다 — 승인 게이트 ㉠ 은 05·06 이 진다. 이 화면에
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
        api.patch<EventItemUpdateResponse>(`/event-items/${item.item_id}`, {
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
}: {
  childId: string;
  date: string;
  data: CalendarDayResponse;
  /** 편집이 끝났다(저장했거나 취소했다). 부모가 "일기 쓰기" 상태를 되돌린다. */
  onDone: () => void;
}) {
  const queryClient = useQueryClient();
  const saved = data.diary?.text ?? "";
  const [draft, setDraft] = useState(saved);
  const [editing, setEditing] = useState(saved === "");

  const save = useMutation({
    mutationFn: (text: string) => {
      // 🚨 PUT 이라 통째로 덮는다. 사진과 이어 둔 일정을 함께 실어 보내지 않으면 지워진다.
      const body: CalendarDayUpdate = {
        text,
        image_urls: data.diary?.image_urls ?? [],
        event_ids: data.events.map((event) => event.id),
      };
      return api.put<CalendarDayResponse>(`/children/${childId}/calendar/${date}`, body);
    },
    onSuccess: () => {
      setEditing(false);
      onDone();
      void queryClient.invalidateQueries({ queryKey: qk.calendarDay(childId, date) });
      // 달 그리드의 일기 표식이 바뀐다.
      void queryClient.invalidateQueries({ queryKey: qk.child(childId) });
    },
  });

  if (!editing) {
    return (
      <div className="flex flex-col gap-2">
        <div className="rounded-card bg-surface border-line border p-4">
          {/* 🚨 `dangerouslySetInnerHTML` 을 쓰지 않는다. 줄바꿈은 CSS 로 살린다. */}
          <p className="text-body text-ink whitespace-pre-wrap">{saved}</p>
        </div>
        <div>
          <Button variant="tertiary" size="compact" onClick={() => setEditing(true)}>
            일기 고치기
          </Button>
        </div>
        <DiaryNote />
      </div>
    );
  }

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
          disabled={save.isPending || draft.trim() === saved.trim()}
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

function Photos({ urls }: { urls: string[] }) {
  return (
    <ul className="grid grid-cols-2 gap-2">
      {urls.map((url) => (
        <li key={url} className="rounded-card bg-surface-muted overflow-hidden">
          {/*
            next/image 를 쓰지 않는다 — 사진 주소는 서버가 주는 임의 호스트라 `remotePatterns` 에
            미리 적을 수 없고, 배포마다 달라지면 이미지가 조용히 404 가 된다.
            🚨 `alt` 를 **비워 두지 않는다.** 빈 `alt` 는 "장식이라 안 읽어도 된다" 는 뜻인데
               이건 내용이다. 사진 안에 무엇이 있는지는 우리가 모르므로 **무엇의 사진인지**만
               말한다 — 없는 설명을 지어내는 것과 통째로 숨기는 것 사이의 정직한 자리다.
          */}
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src={url}
            alt="이날 캘린더에 남은 사진"
            className="aspect-[4/3] w-full object-cover"
            loading="lazy"
          />
        </li>
      ))}
    </ul>
  );
}
