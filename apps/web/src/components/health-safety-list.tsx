"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { josa } from "es-hangul";
import { ShieldAlert } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { Banner } from "@/components/ui/banner";
import { BottomSheet } from "@/components/ui/bottom-sheet";
import { Button } from "@/components/ui/button";
import { Select } from "@/components/ui/select";
import { Spinner } from "@/components/ui/spinner";
import { TextInput } from "@/components/ui/text-input";
import { formatDay } from "@/lib/format";
import {
  addHealthSafety,
  api,
  isApiError,
  qk,
  type CreateHealthSafetyRequest,
  type HealthSafety,
} from "@/lib/api";
import { useIdempotencyKey } from "@/lib/api/use-idempotency-key";

/**
 * 11 알레르기 · 건강 기록 — 🚨 **승인 게이트 ㉡.**
 *
 * 🚨 **이 목록은 LLM 이 만들거나 고치지 못하는 유일한 데이터다** (최상위 CLAUDE.md §2 · NF-03).
 *    쓰기 경로는 `POST /children/{cid}/health-safety` 하나뿐이고 보호자 토큰으로만 열린다.
 *    화면에서도 그 사실이 보여야 해서, 이 구역만 다른 무게로 선다 — 등록은 `caution` 배너 +
 *    `btn-approve`, 회수는 `btn-danger` 다.
 *
 * 🚨 **도메인 색(`health`)을 쓰지 않는다.** 도메인 색의 뜻은 "어느 Agent 결과인가" 인데
 *    (넓혀도 "어느 영역인가") 이 기록은 Agent 가 만지지 못하는 것이다. 플럼을 칠하면 화면이
 *    규칙의 반대말을 한다. 색을 가져가는 것은 `caution` · `danger` 둘뿐이다.
 *
 * 🚨 **낙관적 업데이트를 쓰지 않는다** (apps/web/CLAUDE.md §3). 서버가 확정하기 전에 목록에
 *    먼저 그리면 "되돌릴 수 없는 것은 사람이 승인한다" 가 시각적으로 깨진다.
 * 🚨 **재시도에 키를 새로 만들지 않는다.** 같은 키를 다시 보내는 것이 중복 저장을 막는 장치다.
 *
 * 🚨 **회수는 행을 지우는 것이 아니라 `retracted` 로 내리는 것이다** (계약서 §10).
 *    화면 문구도 "지워요" 가 아니라 "빠져요" 다 — 없는 동작을 약속하지 않는다.
 */

/** `type` 은 DB 의 `health_safety.kind` 다 (`Ref.kind` 와 이름이 겹쳐 API 에서만 `type`). */
const TYPE_OPTIONS = [
  { value: "allergy", label: "알레르기" },
  { value: "condition", label: "지병 · 만성질환" },
] as const;

const CATEGORY_OPTIONS = [
  { value: "식품", label: "음식" },
  { value: "약", label: "약" },
  { value: "환경", label: "환경 · 계절" },
  { value: "기타", label: "그 밖에" },
] as const;

/**
 * 🚨 **"모르겠어요" 가 기본값이다.** 보호자가 안 고르면 `severity` 를 보내지 않는다 —
 *    심각도는 추측하면 안 되는 값이고(NF-03), 비워 두는 것이 없는 값을 지어내는 것보다 낫다.
 */
const SEVERITY_OPTIONS = [
  { value: "unknown", label: "모르겠어요" },
  { value: "mild", label: "가볍게" },
  { value: "moderate", label: "보통" },
  { value: "severe", label: "심하게" },
] as const;

type TypeValue = (typeof TYPE_OPTIONS)[number]["value"];
type CategoryValue = (typeof CATEGORY_OPTIONS)[number]["value"];
type SeverityValue = (typeof SEVERITY_OPTIONS)[number]["value"];

const SEVERITY_LABEL: Record<string, string> = {
  mild: "가볍게",
  moderate: "보통",
  severe: "심하게",
};

const TYPE_LABEL: Record<string, string> = {
  allergy: "알레르기",
  condition: "지병",
};

/* ── 목록 ─────────────────────────────────────────────────────────────── */

export function HealthSafetyList({
  childId,
  items,
}: {
  childId: string;
  items: HealthSafety[];
}) {
  return (
    <ul className="border-line divide-line rounded-card bg-surface divide-y overflow-hidden border">
      {items.map((item) => (
        <li key={item.id}>
          <HealthSafetyRow childId={childId} item={item} />
        </li>
      ))}
    </ul>
  );
}

function HealthSafetyRow({ childId, item }: { childId: string; item: HealthSafety }) {
  const queryClient = useQueryClient();
  const [confirming, setConfirming] = useState(false);

  /**
   * 🚨 **확인 패널을 화면 안으로 끌어온다.** 목록 중간의 줄을 누르면 패널이 하단 네비 뒤에
   *    열려서, 부모가 보기에는 **아무 일도 안 일어난 것**이 된다 (실제로 그렇게 보였다).
   *    되돌릴 수 없는 확정을 묻는 자리라 "안 보이는 확인" 이 제일 나쁘다.
   */
  const confirmRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (confirming) confirmRef.current?.scrollIntoView({ block: "nearest" });
  }, [confirming]);

  /**
   * 🚨 회수도 게이트 안이다 (계약서 §10 이 `GET`·`POST`·`DELETE` 를 한 묶음으로 둔다).
   *    다만 `Idempotency-Key` 는 요구하지 않는다 — 필수 5개는 전부 POST 이고, 같은 회수를
   *    두 번 보내도 결과가 같다.
   */
  const retract = useMutation({
    mutationFn: () => api.delete<void>(`/children/${childId}/health-safety/${item.id}`),
    onSuccess: async () => {
      // 🚨 성공한 뒤에만 닫는다. 실패하면 남는다 — 다시 누르는 것이 재시도다.
      setConfirming(false);
      // 안전 정보 하나가 Food Agent 의 실행 조건까지 바꾼다. 아이 스코프를 통째로 무효화한다.
      await queryClient.invalidateQueries({ queryKey: qk.child(childId) });
    },
  });

  const meta = [TYPE_LABEL[item.type] ?? item.type, item.category].filter(Boolean).join(", ");
  const severity = item.severity ? SEVERITY_LABEL[item.severity] : null;

  return (
    <div className="flex flex-col">
      <div className="flex items-start gap-3 px-4 py-3.5">
        <div className="flex min-w-0 flex-1 flex-col gap-1">
          <p className="text-body text-ink">{item.label}</p>
          {/* 🚨 띄운 가운뎃점은 줄당 하나다 (디자인 시스템 §4) — 나머지는 쉼표로 잇는다. */}
          <p className="text-caption text-ink-subtle">
            {meta}
            {severity ? ` · ${severity}` : ""}
          </p>
          {item.reactions.length > 0 ? (
            <p className="text-caption text-ink-muted">증상 {item.reactions.join(", ")}</p>
          ) : null}
          {item.notes ? <p className="text-caption text-ink-muted">{item.notes}</p> : null}
          {/* 🚨 **누가 언제 확정했는지를 적는다.** 이 제품은 추천에 근거를 달고 나가는데
              (PRODUCT.md), 부모가 가장 믿어야 하는 이 한 줄에만 출처가 없으면 앞뒤가 안 맞는다.
              공유 계정이라 배우자·조부모가 넣은 것일 수도 있다. */}
          <p className="text-caption text-ink-subtle">
            {item.created_by ? `${item.created_by.nickname}님이 ` : ""}
            {formatDay(item.updated_at)}에 확인
          </p>
        </div>

        <Button
          variant="tertiary"
          size="compact"
          onClick={() => setConfirming((open) => !open)}
          className="shrink-0"
        >
          내리기
        </Button>
      </div>

      {confirming ? (
        <div
          ref={confirmRef}
          // 🚨 `scroll-mb-*` 가 없으면 `scrollIntoView` 가 하단 네비 **뒤**를 "보인다" 고
          //    판단해서 버튼이 그대로 가려진다. 네비 높이(51px)보다 넉넉히 준다.
          className="bg-surface-muted border-line flex scroll-mb-28 flex-col gap-3 border-t px-4 py-3.5"
        >
          {/* 🚨 **무엇이 없어지는지 먼저 보여준다.** 이 기록이 빠지면 식사 제안이 이 항목을
              거르지 않게 된다 — 그 사실이 회수 버튼 옆에 있어야 한다.
              🚨 예측을 쓰지 않는다. 일어날 일만 적는다. */}
          <p className="text-body-sm text-ink-muted">
            {/* 🚨 라벨에 조사를 박지 않는다 — 받침으로 고르는 것은 `josa` 다
                (apps/web/CLAUDE.md §3 · 옵션 키는 받침형이 앞이다). */}
            {josa(item.label, "이/가")} 안전 정보에서 빠져요. 앞으로 식사 제안이 이 항목을
            거르지 않아요.
          </p>
          {retract.isError ? (
            <p className="text-caption text-danger-ink">
              {retract.error instanceof Error ? retract.error.message : "내리지 못했어요"}
            </p>
          ) : null}
          <div className="flex gap-2">
            <Button
              variant="danger"
              size="compact"
              onClick={() => retract.mutate()}
              disabled={retract.isPending}
              className="flex-1"
            >
              {retract.isPending ? <Spinner /> : null}
              {retract.isPending ? "내리는 중이에요" : "내릴게요"}
            </Button>
            <Button
              variant="tertiary"
              size="compact"
              onClick={() => setConfirming(false)}
              disabled={retract.isPending}
              className="flex-1"
            >
              그대로 둘게요
            </Button>
          </div>
        </div>
      ) : null}
    </div>
  );
}

/* ── 🚨 승인 게이트 ㉡ — 등록 시트 ───────────────────────────────────── */

export function HealthSafetySheet({
  open,
  onClose,
  childId,
}: {
  open: boolean;
  onClose: () => void;
  childId: string;
}) {
  const queryClient = useQueryClient();

  const [type, setType] = useState<TypeValue>("allergy");
  const [label, setLabel] = useState("");
  const [category, setCategory] = useState<CategoryValue>("식품");
  const [severity, setSeverity] = useState<SeverityValue>("unknown");
  const [reactions, setReactions] = useState("");
  const [notes, setNotes] = useState("");
  const [labelError, setLabelError] = useState<string | null>(null);

  /** 🚨 사용자 동작 하나에 키 하나. `mutationFn` 안에서 새로 만들지 않는다. */
  const idempotencyKey = useIdempotencyKey();

  const save = useMutation({
    mutationFn: (body: CreateHealthSafetyRequest) =>
      addHealthSafety(childId, body, idempotencyKey.current()),
    onSuccess: async () => {
      // 🚨 다음 동작으로 넘어가는 것은 성공 응답을 받은 뒤다 (실패 뒤 재시도는 같은 키).
      idempotencyKey.rotate();
      await queryClient.invalidateQueries({ queryKey: qk.child(childId) });
      reset();
      onClose();
    },
  });

  function reset() {
    setType("allergy");
    setLabel("");
    setCategory("식품");
    setSeverity("unknown");
    setReactions("");
    setNotes("");
    setLabelError(null);
    save.reset();
  }

  function submit() {
    const trimmed = label.trim();
    if (!trimmed) {
      setLabelError("무엇인지 적어주세요");
      return;
    }
    setLabelError(null);

    const reactionList = reactions
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);

    save.mutate({
      type,
      label: trimmed,
      category,
      // 🚨 "모르겠어요" 는 값을 안 보내는 것이다. 빈 문자열이나 기본 심각도를 지어내지 않는다.
      ...(severity !== "unknown" ? { severity } : {}),
      ...(reactionList.length > 0 ? { reactions: reactionList } : {}),
      ...(notes.trim() ? { notes: notes.trim() } : {}),
    });
  }

  const alreadyExists = isApiError(save.error, "already_exists");
  const consentRequired = isApiError(save.error, "consent_required");

  return (
    <BottomSheet
      open={open}
      onClose={() => {
        reset();
        onClose();
      }}
      title="알레르기 · 건강 기록 추가"
      // 🚨 승인 시트는 스크림 탭·ESC 로 닫히지 않는다 (디자인 시스템 §7). 나가는 길은 아래 버튼이다.
      dismissible={false}
      footer={
        <div className="flex flex-col gap-2">
          <Button variant="approve" onClick={submit} disabled={save.isPending}>
            {save.isPending ? <Spinner /> : null}
            {save.isPending ? "등록하는 중이에요" : "확인했어요, 등록할게요"}
          </Button>
          <Button
            variant="tertiary"
            block
            onClick={() => {
              reset();
              onClose();
            }}
            disabled={save.isPending}
          >
            그만두기
          </Button>
        </div>
      }
    >
      <div className="flex flex-col gap-4">
        {/* 🚨 `caution` 은 승인 게이트 2곳 전용이다 (디자인 시스템 §3). 여기가 그중 하나다. */}
        <Banner tone="caution" title="보호자가 확인한 것만 적어주세요">
          여기 적은 것은 AI 가 고치거나 지우지 못해요. 식사 제안은 이 기록만 보고 거르기 때문에,
          들은 이야기가 아니라 직접 확인한 것만 남기는 편이 안전해요.
        </Banner>

        <Select label="종류" value={type} options={TYPE_OPTIONS} onChange={setType} />

        <TextInput
          label="무엇인가요"
          hint="한 번에 하나씩 적어요. 우유, 땅콩처럼 짧게."
          value={label}
          onChange={(e) => setLabel(e.target.value)}
          error={labelError}
          autoComplete="off"
        />

        <Select label="분류" value={category} options={CATEGORY_OPTIONS} onChange={setCategory} />

        <Select
          label="얼마나 심한가요"
          value={severity}
          options={SEVERITY_OPTIONS}
          onChange={setSeverity}
        />

        <TextInput
          label="증상 · 선택"
          hint="쉼표로 나눠 적어요. 두드러기, 기침"
          value={reactions}
          onChange={(e) => setReactions(e.target.value)}
          autoComplete="off"
        />

        <TextInput
          label="메모 · 선택"
          hint="언제 어디서 확인했는지 적어 두면 나중에 도움이 돼요."
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          autoComplete="off"
        />

        {/* 🚨 실패를 한 덩어리로 뭉뚱그리지 않는다 — 다시 눌러야 하는 것과 다른 곳으로 가야
            하는 것은 다른 말이다 (apps/web/CLAUDE.md §3 에러). */}
        {alreadyExists ? (
          <p className="text-body-sm text-ink-muted">
            이미 등록된 항목이에요. 목록에서 확인할 수 있어요.
          </p>
        ) : consentRequired ? (
          <p className="text-body-sm text-ink-muted">
            건강 정보 동의를 받기 전이라 저장된 것은 하나도 없어요. 동의 화면은 아직 없어요.
          </p>
        ) : save.isError ? (
          <p className="text-body-sm text-ink-muted">
            등록하지 못했어요. 저장된 것은 하나도 없으니 다시 눌러 주세요.
          </p>
        ) : null}
      </div>
    </BottomSheet>
  );
}

/** 구역 제목 옆에 서는 아이콘. 목록이 비어 있을 때 `EmptyState` 가 쓴다. */
export const HEALTH_SAFETY_ICON = ShieldAlert;
