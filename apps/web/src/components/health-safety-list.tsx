"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { josa } from "es-hangul";
import { CircleMinus, Pencil, Shield } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { Banner } from "@/components/ui/banner";
import { BottomSheet } from "@/components/ui/bottom-sheet";
import { Button } from "@/components/ui/button";
import { ICON_SIZE, ICON_STROKE } from "@/components/ui/icon";
import { IconButton } from "@/components/ui/icon-button";
import { IconTile } from "@/components/ui/icon-tile";
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
  type UpdateHealthSafetyRequest,
  type UpdateHealthSafetyResponse,
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
  onEdit,
}: {
  childId: string;
  items: HealthSafety[];
  onEdit: (item: HealthSafety) => void;
}) {
  return (
    <ul className="border-line divide-line rounded-card bg-surface divide-y overflow-hidden border">
      {items.map((item) => (
        <li key={item.id}>
          <HealthSafetyRow childId={childId} item={item} onEdit={() => onEdit(item)} />
        </li>
      ))}
    </ul>
  );
}

function HealthSafetyRow({
  childId,
  item,
  onEdit,
}: {
  childId: string;
  item: HealthSafety;
  onEdit: () => void;
}) {
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
        {/* 🚨 **측정 기록 줄과 같은 타일을 세운다** (apps/web/CLAUDE.md §3 — 한 화면의 두
            목록은 같은 기준선에서 시작한다). 없으면 두 "훑는 목록" 의 왼쪽 들여쓰기가 달라
            남남으로 읽힌다. 🚨 뉴트럴이다 — 도메인 `health` 색은 "Health Agent 결과" 라는
            뜻이고, 이 기록은 Agent 가 만지지 못하는 것이다.
            🚨 `ShieldAlert` 가 아니라 `Shield` 다. 줄마다 경고 표시가 서면 등록해 둔 사실이
            매번 사고처럼 읽힌다 — 이 화면은 알리는 자리가 아니라 관리하는 자리다. */}
        <IconTile icon={Shield} tone="neutral" />

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

        {/* 🚨 **행동 둘이 아이콘이다** (측정 기록 줄과 같은 처리). 글자 버튼 둘을 나란히 두면
            360px 에서 줄이 두 겹으로 접히고, 이름보다 버튼이 넓어진다. 뜻은 `IconButton` 의
            `label`(스크린리더 이름 + 툴팁)이 지고, 누르면 열리는 패널·시트가 글자로 다시 말한다.
            🚨 내리기에 `Trash2` 를 쓰지 않는다 — 지우는 게 아니라 **내리는** 것이다 (행은 남는다). */}
        <div className="-my-1 flex shrink-0 items-center">
          <IconButton label="이 기록 고치기" onClick={onEdit}>
            <Pencil aria-hidden size={ICON_SIZE.md} strokeWidth={ICON_STROKE} />
          </IconButton>
          <IconButton label="이 기록 내리기" onClick={() => setConfirming((open) => !open)}>
            <CircleMinus aria-hidden size={ICON_SIZE.md} strokeWidth={ICON_STROKE} />
          </IconButton>
        </div>
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
          {/* 🚨 **실패를 빨강으로 칠하지 않는다** (디자인 시스템 §3 · §5). `danger` 는
              알레르기·건강 중단·파괴적 확정에만 쓰고, 실패는 `ink-muted` 다.
              🚨 **서버가 준 문구를 그대로 싣지 않는다** — 에러 문구는 프론트가 만든다. */}
          {retract.isError ? (
            <p role="status" className="text-body-sm text-ink-muted">
              내리지 못했어요. 아직 안전 정보에 그대로 있으니 다시 눌러 주세요.
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
  item,
}: {
  open: boolean;
  onClose: () => void;
  childId: string;
  /**
   * 넘기면 **고치기**, 없으면 **등록**이다. 🚨 둘 다 승인 게이트 ㉡ 라 화면이 같다 —
   * `caution` 배너 + `btn-approve`. 게이트를 늘린 것이 아니라 같은 게이트의 다른 동작이다.
   */
  item?: HealthSafety;
}) {
  const queryClient = useQueryClient();
  const editing = item !== undefined;

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

  /**
   * 🚨 **고치기에는 `Idempotency-Key` 를 붙이지 않는다.** 필수 5개는 전부 "두 번 실행되면
   *    두 번 쌓이는" POST 이고, 같은 값으로 두 번 PATCH 해도 결과가 같다.
   * 🚨 낙관적 업데이트를 쓰지 않는다 — 서버가 확정하기 전에 목록을 바꾸면 "되돌릴 수 없는 것은
   *    사람이 승인한다" 가 시각적으로 깨진다 (apps/web/CLAUDE.md §3).
   */
  const update = useMutation({
    mutationFn: (body: UpdateHealthSafetyRequest) =>
      api.patch<UpdateHealthSafetyResponse>(
        `/children/${childId}/health-safety/${item?.id}`,
        body,
      ),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: qk.child(childId) });
      reset();
      onClose();
    },
  });

  const pending = editing ? update.isPending : save.isPending;
  const failed = editing ? update.isError : save.isError;

  function reset() {
    setType("allergy");
    setLabel("");
    setCategory("식품");
    setSeverity("unknown");
    setReactions("");
    setNotes("");
    setLabelError(null);
    save.reset();
    update.reset();
  }

  /**
   * 🚨 **고치기는 서버 값이 바뀌면 폼을 맞춘다.** 배우자가 같은 항목을 먼저 고쳤을 때 옛 값을
   *    들고 있으면, 승인하는 순간 남의 수정을 되돌린다 (11 기본 정보와 같은 처리).
   *    effect 가 아니라 렌더 중 조정이다 — effect 면 옛 값으로 한 프레임을 먼저 그린다.
   */
  const itemKey = item ? `${item.id}\u0000${item.updated_at}` : null;
  /**
   * 🚨 **`useState(itemKey)` 로 시작하면 안 된다.** 첫 렌더에서 이미 같은 값이라 비교가
   *    통과해 버리고, **폼이 기본값인 채로 열린다** — 고치기 시트가 "무엇인가요" 를 비운 채
   *    떴다 (실제로 그랬다). `null` 로 시작해야 첫 렌더에서 한 번 맞춘다.
   *    등록 모드는 `item` 이 없어서 이 분기에 아예 안 들어온다.
   */
  const [syncedItem, setSyncedItem] = useState<string | null>(null);
  if (item && syncedItem !== itemKey) {
    setSyncedItem(itemKey);
    {
      setType((TYPE_LABEL[item.type] ? item.type : "allergy") as TypeValue);
      setLabel(item.label);
      setCategory((item.category || "기타") as CategoryValue);
      setSeverity((item.severity && SEVERITY_LABEL[item.severity] ? item.severity : "unknown") as SeverityValue);
      setReactions(item.reactions.join(", "));
      setNotes(item.notes ?? "");
      setLabelError(null);
    }
  }

  function submit() {
    const reactionList = reactions
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);

    if (editing) {
      // 🚨 종류·이름은 보내지 않는다 — 그 둘은 이 기록의 정체다 (`UpdateHealthSafetyRequest`).
      // 🚨 "모르겠어요" 로 되돌리는 것은 `null` 이다. 키를 빼면 "그대로 두기" 가 된다.
      update.mutate({
        category,
        severity: severity === "unknown" ? null : severity,
        reactions: reactionList,
        notes: notes.trim() ? notes.trim() : null,
      });
      return;
    }

    const trimmed = label.trim();
    if (!trimmed) {
      setLabelError("무엇인지 적어주세요");
      return;
    }
    setLabelError(null);

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
  const consentRequired = isApiError(editing ? update.error : save.error, "consent_required");

  return (
    <BottomSheet
      open={open}
      onClose={() => {
        reset();
        onClose();
      }}
      title={editing ? "알레르기 · 건강 기록 고치기" : "알레르기 · 건강 기록 추가"}
      // 🚨 승인 시트는 스크림 탭·ESC 로 닫히지 않는다 (디자인 시스템 §7). 나가는 길은 아래 버튼이다.
      dismissible={false}
      footer={
        <div className="flex flex-col gap-2">
          {/* 🚨 **실패 문구가 버튼 옆에 있어야 한다.** 예전에는 시트 본문 맨 아래였는데,
              본문은 스크롤하고 버튼은 고정이라 390px 폰에서는 **되돌릴 수 없는 등록이
              실패한 사실이 화면에 하나도 안 보였다.** 확인 패널에 `scrollIntoView` 를 단 것과
              같은 사고다 — 여기는 고정 영역으로 옮겨서 애초에 스크롤 밖으로 나가지 않게 한다.
              🚨 실패를 한 덩어리로 뭉뚱그리지 않는다 — 다시 눌러야 하는 것과 다른 곳으로
              가야 하는 것은 다른 말이다 (apps/web/CLAUDE.md §3 에러). */}
          {alreadyExists ? (
            <p role="status" className="text-body-sm text-ink-muted">
              이미 등록된 항목이에요. 목록에서 확인할 수 있어요.
            </p>
          ) : consentRequired ? (
            <p role="status" className="text-body-sm text-ink-muted">
              건강 정보 동의를 받기 전이라 저장된 것은 하나도 없어요. 동의 화면은 아직 없어요.
            </p>
          ) : failed ? (
            <p role="status" className="text-body-sm text-ink-muted">
              {editing
                ? "고치지 못했어요. 기록은 고치기 전 그대로니 다시 눌러 주세요."
                : "등록하지 못했어요. 저장된 것은 하나도 없으니 다시 눌러 주세요."}
            </p>
          ) : null}

          <Button variant="approve" onClick={submit} disabled={pending}>
            {pending ? <Spinner /> : null}
            {pending
              ? editing
                ? "고치는 중이에요"
                : "등록하는 중이에요"
              : editing
                ? "확인했어요, 고칠게요"
                : "확인했어요, 등록할게요"}
          </Button>
          <Button
            variant="tertiary"
            block
            onClick={() => {
              reset();
              onClose();
            }}
            disabled={pending}
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

        {/* 🚨 **고칠 때는 종류와 이름이 입력이 아니다.** 그 둘은 이 기록의 정체라서, 바꾸는 것은
            고치기가 아니라 다른 기록이다 — `UNIQUE(child_id, type, label)` 과 "이미 등록된 항목"
            판정이 같이 흔들린다. 입력을 비활성으로 두는 대신 **아예 글자로** 세운다: 비활성
            입력은 "왜 안 눌리지" 를 만들고, 브랜드색을 흐리게 만든 비활성과 같은 종류의 나쁨이다
            (디자인 시스템 §2-5). 바꿀 길은 아래 문구가 알려준다. */}
        {editing ? (
          <div className="flex flex-col gap-1.5">
            <span className="text-label text-ink-muted">무엇인가요</span>
            {/* 🚨 이름이 먼저다. 종류는 뒤에 쉼표로 붙이되, 이름이 비면 붙이지 않는다 —
                앞이 빈 채 쉼표로 시작하는 줄이 실제로 나왔다. */}
            <p className="text-body text-ink">
              {[label, TYPE_LABEL[type]].filter(Boolean).join(", ")}
            </p>
            <p className="text-caption text-ink-subtle">
              종류와 이름은 고칠 수 없어요. 다른 항목이면 이 기록을 내리고 새로 등록해 주세요.
            </p>
          </div>
        ) : (
          <>
            <Select label="종류" value={type} options={TYPE_OPTIONS} onChange={setType} />

            <TextInput
              label="무엇인가요"
              hint="한 번에 하나씩 적어요. 우유, 땅콩처럼 짧게."
              value={label}
              onChange={(e) => setLabel(e.target.value)}
              error={labelError}
              autoComplete="off"
            />
          </>
        )}

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

      </div>
    </BottomSheet>
  );
}
