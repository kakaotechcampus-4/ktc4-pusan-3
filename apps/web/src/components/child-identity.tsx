"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { UserRound } from "lucide-react";
import { useState, type ReactNode } from "react";

import { BottomSheet } from "@/components/ui/bottom-sheet";
import { Button } from "@/components/ui/button";
import { DateField } from "@/components/ui/date-field";
import { IconTile } from "@/components/ui/icon-tile";
import { ChoiceField } from "@/components/ui/choice-field";
import { Spinner } from "@/components/ui/spinner";
import { TextInput } from "@/components/ui/text-input";
import { formatDateWithYear } from "@/lib/format";
import { api, qk, type ChildProfile, type Gender, type UpdateChildRequest } from "@/lib/api";
import { GENDER_LABEL, GENDER_OPTIONS } from "@/lib/gender";

/* ── 보여주는 카드 ────────────────────────────────────────────────────── */

export function ChildIdentityCard({ profile }: { profile: ChildProfile }) {
  return (
    <div className="rounded-card border-line bg-surface flex flex-col border">
      {/* 이름이 이 카드의 주어다. 나이는 서버 문구를 그대로 그린다 (CLAUDE.md §3). */}
      <div className="flex items-center gap-3 px-4 py-4">
        <IconTile icon={UserRound} />
        <div className="min-w-0">
          <p className="text-title text-ink">{profile.nickname}</p>
          <p className="text-body-sm text-ink-muted mt-0.5">{profile.age_display}</p>
        </div>
      </div>

      {/* 🚨 나머지는 **이름 아래 딸린 것**이라 선 하나로 갈고 작게 쌓는다. 같은 크기로 세 줄을
          늘어놓으면 별명이 다른 둘과 같은 무게가 되어 카드에 주어가 사라진다. */}
      <dl className="border-line divide-line flex flex-col divide-y border-t">
        <IdentityRow term="생일" value={formatDateWithYear(profile.birth_date)} />
        <IdentityRow term="성별" value={GENDER_LABEL[profile.gender]} />
      </dl>
    </div>
  );
}

/**
 * 🚨 **값이 오른쪽에 붙는다.** 이름표를 왼쪽에 세우고 값을 오른쪽 끝에 두면 두 줄의 값이
 *    같은 선에서 시작해 훑을 기준선이 생긴다. 🚨 값이 길어 줄바꿈되면 이름표가 위로 붙게
 *    `items-start` 다 — 가운데 정렬로 두면 두 줄짜리 값 옆에서 이름표가 떠 보인다.
 */
function IdentityRow({ term, value }: { term: string; value: ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-4 px-4 py-3">
      <dt className="text-label text-ink-muted shrink-0">{term}</dt>
      <dd className="text-body-sm text-ink text-right">{value}</dd>
    </div>
  );
}

/* ── 고치는 시트 ──────────────────────────────────────────────────────── */

export function ChildIdentitySheet({
  open,
  onClose,
  childId,
  profile,
  earliestBirthDate,
}: {
  open: boolean;
  onClose: () => void;
  childId: string;
  profile: ChildProfile;
  /** 달력의 하한. 🚨 나이 계산이 아니라 고를 수 있는 범위다 (apps/web/CLAUDE.md §4 예외). */
  earliestBirthDate: Date;
}) {
  const queryClient = useQueryClient();

  const [nickname, setNickname] = useState(profile.nickname);
  const [birthDate, setBirthDate] = useState(profile.birth_date);
  const [gender, setGender] = useState<Gender>(profile.gender);
  const [nicknameError, setNicknameError] = useState<string | null>(null);

  /**
   * 🚨 **서버 값이 바뀌면 손대지 않은 칸만 그 값으로 맞춘다.** 배우자가 같은 아이를 고쳐서
   *    쿼리가 새로 받아와도 폼이 옛 값을 들고 있으면, 아래 `changes` 가 **남이 방금 저장한
   *    값을 내 수정분으로 잡고** 승인하는 순간 그 수정을 되돌린다 — 공유 계정이라 실제로 나는
   *    경로다 (PRODUCT.md).
   * 🚨 **칸 단위다.** 세 칸을 통째로 덮으면 배우자가 생일을 고친 순간 내가 적고 있던 별명이
   *    말없이 사라진다. ⚠️ 같은 칸을 둘이 동시에 고치는 경우는 남는다 — 충돌이라 화면이 혼자
   *    못 정하고, 지금은 내 입력을 지키는 쪽이다.
   * 🚨 effect 가 아니라 렌더 중 조정이다 (React "Adjusting state when props change") —
   *    effect 면 옛 값으로 한 프레임을 먼저 그린다.
   */
  const [synced, setSynced] = useState(() => ({
    nickname: profile.nickname,
    birth_date: profile.birth_date,
    gender: profile.gender,
  }));
  if (
    synced.nickname !== profile.nickname ||
    synced.birth_date !== profile.birth_date ||
    synced.gender !== profile.gender
  ) {
    if (nickname === synced.nickname) setNickname(profile.nickname);
    if (birthDate === synced.birth_date) setBirthDate(profile.birth_date);
    if (gender === synced.gender) setGender(profile.gender);
    setSynced({
      nickname: profile.nickname,
      birth_date: profile.birth_date,
      gender: profile.gender,
    });
  }

  /**
   * 🚨 **고친 것만 보낸다.** 안 고친 필드를 함께 올리면 두 보호자가 같은 화면을 열어 뒀을 때
   *    나중 저장이 남의 수정을 덮는다.
   */
  const changes: UpdateChildRequest = {
    ...(nickname.trim() !== profile.nickname ? { nickname: nickname.trim() } : {}),
    ...(birthDate !== profile.birth_date ? { birth_date: birthDate } : {}),
    ...(gender !== profile.gender ? { gender } : {}),
  };
  const dirty = Object.keys(changes).length > 0;

  const save = useMutation({
    mutationFn: (body: UpdateChildRequest) =>
      api.patch<{ child: ChildProfile }>(`/children/${childId}`, body),
    onSuccess: async () => {
      // 별명 하나가 홈 인사말과 `GET /me` 의 아이 목록까지 바꾼다. 아이 스코프를 통째로 무효화한다.
      await queryClient.invalidateQueries({ queryKey: qk.child(childId) });
      await queryClient.invalidateQueries({ queryKey: qk.me() });
      // 🚨 성공해야 닫는다. 실패하면 시트가 남아서 고친 값이 그대로 있다 — 다시 누르는 것이 재시도다.
      onClose();
    },
  });

  function submit() {
    if (!nickname.trim()) {
      setNicknameError("별명을 적어주세요");
      return;
    }
    setNicknameError(null);
    save.mutate(changes);
  }

  return (
    <BottomSheet
      open={open}
      onClose={onClose}
      title="기본 정보 고치기"
      // 🚨 승인 게이트가 아니라 스크림 탭으로 닫힌다 — 되돌릴 수 있는 값이다 (위 머리말).
      footer={
        <div className="flex flex-col gap-2">
          {/* 🚨 `role="status"` 로 알린다 — 실패를 눈으로만 알 수 있게 두지 않는다.
              🚨 실패를 빨강으로 칠하지 않는다 (디자인 시스템 §3). */}
          {save.isError ? (
            <p role="status" className="text-body-sm text-ink-muted">
              저장하지 못했어요. 고친 것은 그대로 있으니 다시 눌러 주세요.
            </p>
          ) : null}

          {/* 🚨 시트가 자기 primary 를 갖는다 — 모달이라 뒤가 `inert` 다 (§7 인라인이냐 모달이냐). */}
          <Button block onClick={submit} disabled={!dirty || save.isPending}>
            {save.isPending ? <Spinner /> : null}
            {save.isPending ? "저장하는 중이에요" : "고친 것 저장하기"}
          </Button>
          <Button variant="tertiary" block onClick={onClose} disabled={save.isPending}>
            그만두기
          </Button>
        </div>
      }
    >
      <div className="flex flex-col gap-4">
        <TextInput
          label="별명"
          value={nickname}
          onChange={(e) => setNickname(e.target.value)}
          error={nicknameError}
          autoComplete="off"
        />

        <DateField
          label="생일"
          hint="나이는 생일을 보고 서버가 계산해요."
          value={birthDate}
          onChange={setBirthDate}
          fromDate={earliestBirthDate}
          toDate={new Date()}
        />

        {/* ⚠️ 필수값이다 — 비워 둘 수 없다 (#75 · 위 `GENDER_OPTIONS` 주석).
            🚨 성별은 **화면 표시 전용**이라는 제약은 그대로다: Agent 컨텍스트에 넣지 않는다. */}
        <ChoiceField label="성별" value={gender} options={GENDER_OPTIONS} onChange={setGender} />
      </div>
    </BottomSheet>
  );
}
