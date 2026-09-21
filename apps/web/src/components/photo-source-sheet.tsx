"use client";

import { Baby, Camera, ChevronLeft, ChevronRight, ClipboardList, Images } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { BottomSheet } from "@/components/ui/bottom-sheet";
import { Button } from "@/components/ui/button";
import { ICON_SIZE, ICON_STROKE } from "@/components/ui/icon";
import { IconTile } from "@/components/ui/icon-tile";
import { cn } from "@/lib/cn";
import { listRecentPhotos, readRecentPhoto, type RecentPhoto } from "@/lib/native/recent-photos";
import { PHOTO_LANES, type PhotoLane } from "@/lib/api/types";

/**
 * 사진 한 장을 어떻게 읽을지 **고르고**, 어디서 가져올지 고르는 시트 (03 홈 · 09 캘린더 · 08).
 *
 * 🚨 **두 단계다. 한 화면에 쌓지 않는다.** ㉠ 어떤 사진인가 → ㉡ 어디서 가져오나.
 *    한 화면에 다 세워 두고 앞을 안 고르면 뒤를 비활성으로 두는 방식을 먼저 만들었다가 바꿨다 —
 *    **부모가 할 일이 하나인 순간에 화면이 세 덩어리를 보여 주고 있었고**, 꺼진 것들이
 *    "왜 안 눌리지" 를 먼저 묻게 만들었다. 한 번에 한 가지만 묻는다.
 *
 * 🚨 **어떤 사진인지를 먼저 묻는 이유.** 계약서 §09 는 서버가 `lane` 을 추측해 보내 주지만,
 *    추측을 정정하는 것보다 **부모가 선언하는 쪽이 정확하다** — 부모는 무엇을 찍었는지 알고
 *    있고, 그 선언이 있으면 서버 추측은 "다르게 읽혔다" 를 알리는 용도로 내려간다.
 *    문서(`document`)와 아이 활동(`activity`)은 뒤따르는 규칙이 통째로 다르다:
 *    문서는 글자만 읽어 `institution_notice` 로 저장하고, 활동은 태그만 뽑아 보호자 확인으로
 *    저장한다. 그래서 이건 화면 장식이 아니라 **저장 경로를 가르는 선택**이다.
 *
 * 🚨 **1단계는 라디오가 아니라 길이다.** 고르면 곧바로 2단계로 넘어가므로 확인 버튼이 없고,
 *    그래서 `role="radio"` 가 아니라 쉐브론 달린 줄이다 — 누르면 무슨 일이 일어나는지가
 *    모양에 적혀 있어야 한다. 되돌아가는 길은 2단계 맨 위에 "다른 종류로 바꾸기" 로 둔다.
 *
 * 🚨 **최근 사진 줄은 셸이 있을 때만 선다.** 웹은 기기 갤러리를 읽을 수 없다
 *    (`lib/native/recent-photos.ts`). 브라우저에서는 줄 자체가 없고, 빈 상자도 권한 안내도
 *    띄우지 않는다 — 브라우저에는 허용할 권한이 아예 없다.
 *
 * 🚨 **입력이 두 개인 이유는 `capture` 때문이다.** `accept="image/*"` 만 주면 모바일 브라우저가
 *    "촬영 / 앨범" 선택지를 자기 방식으로 한 번 더 띄운다 — 우리 시트에서 이미 고른 것을
 *    OS 가 또 묻는 꼴이다. `capture="environment"` 가 붙은 입력은 카메라로 바로 간다.
 *    ⚠️ 데스크톱 브라우저는 `capture` 를 무시하고 둘 다 파일 대화상자를 연다. 실제 사용 기기가
 *    폰이라(PRODUCT.md) 그 쪽을 기준으로 잡았고, 데스크톱에서는 두 줄이 같은 일을 한다.
 *
 * 🚨 **고른 파일을 여기서 올리지 않는다.** 이 컴포넌트는 `{ file, lane }` 을 돌려주고 끝난다 —
 *    업로드·스트림·저장은 전부 08 화면이 소유한다. 시트가 요청까지 들면 홈과 캘린더가
 *    같은 흐름을 두 벌 갖게 된다.
 */

/** 시트에 몇 장까지 늘어놓는가. 가로로 미는 줄이라 더 받아도 부모가 끝까지 안 민다. */
const RECENT_LIMIT = 12;

interface LaneCopy {
  icon: LucideIcon;
  label: string;
  /** 1단계 줄의 설명. **무엇을 읽는지**를 말한다. */
  hint: string;
  /** 2단계 머리말. 고른 것이 무엇을 뜻하는지 한 줄로 되짚는다. */
  rule: string;
  camera: string;
  album: string;
}

/**
 * 🚨 **`PHOTO_LANES` 와 1:1 이다.** lane 이 늘면 여기서 타입 에러가 난다 —
 *    고를 수 없는 lane 이 생기는 것을 막는 자리다.
 */
const LANE: Record<PhotoLane, LaneCopy> = {
  document: {
    icon: ClipboardList,
    label: "알림장·식단표",
    hint: "글자를 읽어 준비물과 일시로 정리해요",
    rule: "글자만 읽어요. 아이의 기록으로는 쌓지 않아요.",
    camera: "받아 온 알림장을 지금 찍어요",
    album: "찍어 둔 알림장에서 골라요",
  },
  activity: {
    icon: Baby,
    label: "아이 활동 사진",
    hint: "무엇을 하고 놀았는지 활동 태그만 뽑아요",
    rule: "활동 태그만 뽑아요. 얼굴이나 사람은 분석하지 않아요.",
    camera: "지금 모습을 찍어요",
    album: "오늘 찍은 사진에서 골라요",
  },
};

export interface PhotoPick {
  file: File;
  /** 🚨 부모가 **고른** 값이다. 서버 추측이 아니다 — 08 이 이 값을 업로드에 싣는다. */
  lane: PhotoLane;
}

export function PhotoSourceSheet({
  open,
  onClose,
  onPick,
}: {
  open: boolean;
  onClose: () => void;
  onPick: (pick: PhotoPick) => void;
}) {
  const cameraRef = useRef<HTMLInputElement>(null);
  const albumRef = useRef<HTMLInputElement>(null);

  /** `null` 이면 1단계(어떤 사진인가), 값이 있으면 2단계(어디서 가져오나). */
  const [lane, setLane] = useState<PhotoLane | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [recent, setRecent] = useState<RecentPhoto[]>([]);
  const [readingId, setReadingId] = useState<string | null>(null);

  /**
   * 🚨 **시트를 열 때마다 1단계로 되돌린다.** 지난번에 고른 lane 이 남아 있으면 부모가
   *    "어떤 사진인가요" 를 보지 않고 2단계로 열려서 **저장 경로가 지난번 값으로 정해진다.**
   *
   * 🚨 effect 가 아니라 **렌더 중 조정**이다 (React 의 "Adjusting state when a prop changes").
   *    effect 로 하면 지난번 단계가 그려진 뒤에 바뀌어서 한 프레임 보이고, lint 의
   *    `react-hooks/set-state-in-effect` 가 같은 것을 막는다. 이 분기는 렌더가 한 번 더 돌 뿐
   *    화면에 칠해지기 전에 끝난다.
   * 🚨 닫힐 때는 되돌리지 않는다 — 닫히는 동안 화면이 1단계로 되감기는 게 보인다.
   */
  const [wasOpen, setWasOpen] = useState(open);
  if (open !== wasOpen) {
    setWasOpen(open);
    if (open) {
      setLane(null);
      setError(null);
      setReadingId(null);
    }
  }

  /**
   * 최근 사진은 셸에게 묻는다. 🚨 **셸이 없으면 빈 배열**이고 줄 자체가 안 선다.
   * 시트가 열릴 때마다 다시 묻는다 — 그 사이에 새로 찍은 사진이 맨 앞에 와야 한다.
   */
  useEffect(() => {
    if (!open) return;
    let alive = true;
    void listRecentPhotos(RECENT_LIMIT).then((photos) => {
      if (alive) setRecent(photos);
    });
    return () => {
      alive = false;
    };
  }, [open]);

  function pick(file: File) {
    // 1단계를 건너뛰고 여기 오는 경로는 없다. 타입을 좁히려고 한 번 더 본다.
    if (!lane) return;
    setError(null);
    onPick({ file, lane });
  }

  function handleChange(input: HTMLInputElement) {
    const picked = input.files?.[0];
    // 취소는 실패가 아니다 — 아무 말도 하지 않고 시트를 그대로 둔다 (문구 규칙 · PRODUCT.md).
    if (!picked) return;

    // 🚨 `accept` 는 브라우저가 지켜 주기를 기대하는 값이라 여기서 한 번 더 본다.
    if (!picked.type.startsWith("image/")) {
      setError("사진 파일만 넣을 수 있어요.");
      input.value = "";
      return;
    }

    // 🚨 같은 사진을 다시 고를 수 있어야 한다 — 비우지 않으면 change 가 안 뜬다.
    input.value = "";
    pick(picked);
  }

  async function handleRecent(photo: RecentPhoto) {
    setReadingId(photo.id);
    const file = await readRecentPhoto(photo.id);
    setReadingId(null);

    if (!file) {
      // 🚨 실패를 빨강으로 칠하지 않는다. 길도 막지 않는다 — 촬영·앨범이 그대로 남아 있다.
      setError("그 사진을 열지 못했어요. 앨범에서 골라 주세요.");
      return;
    }
    pick(file);
  }

  const chosen = lane ? LANE[lane] : null;

  return (
    <BottomSheet
      open={open}
      onClose={onClose}
      // 🚨 제목이 **지금 묻는 것**을 말한다. 두 단계가 같은 제목을 쓰면 넘어간 것이 안 보인다.
      title={chosen ? chosen.label : "어떤 사진인가요?"}
      description={
        chosen ? chosen.rule : "고른 종류에 따라 읽는 방법이 달라져요. 하나를 골라주세요."
      }
      // 🚨 다른 시트(07 기억 상세 · 06 승인)와 같이 닫는 버튼을 세운다. 웹뷰에는 ESC 가 없어서
      //    스크림 탭 하나만 남으면 닫는 길이 화면에 안 보인다. "취소" 가 아니라 "닫기" 다 —
      //    아직 시작한 것이 없어서 취소할 것도 없다.
      footer={
        <Button variant="secondary" block onClick={onClose}>
          닫기
        </Button>
      }
    >
      {/* 입력은 시트 안에 두고 화면에서는 감춘다 — 누르는 것은 2단계의 줄들이다. */}
      <input
        ref={cameraRef}
        type="file"
        accept="image/*"
        capture="environment"
        className="sr-only"
        tabIndex={-1}
        aria-hidden
        onChange={(e) => handleChange(e.currentTarget)}
      />
      <input
        ref={albumRef}
        type="file"
        accept="image/*"
        className="sr-only"
        tabIndex={-1}
        aria-hidden
        onChange={(e) => handleChange(e.currentTarget)}
      />

      {lane === null ? (
        <LaneStep onChoose={setLane} />
      ) : (
        <SourceStep
          lane={lane}
          recent={recent}
          readingId={readingId}
          onBack={() => {
            setLane(null);
            setError(null);
          }}
          onRecent={handleRecent}
          onCamera={() => cameraRef.current?.click()}
          onAlbum={() => albumRef.current?.click()}
        />
      )}

      {error ? (
        // 🚨 실패를 빨강으로 칠하지 않는다 (디자인 시스템 §3).
        <p
          role="status"
          className="bg-surface-muted rounded-field text-body-sm text-ink-muted mt-3 p-3"
        >
          {error}
        </p>
      ) : null}

      <p className="text-caption text-ink-subtle mt-4">
        읽어낸 내용을 보여드리고, 승인하기 전에는 아무것도 저장하지 않아요.
      </p>
    </BottomSheet>
  );
}

/* ── 1단계 · 어떤 사진인가 ────────────────────────────────────────────── */

/**
 * 🚨 **고르면 바로 다음이다.** 확인 버튼을 두지 않는다 — 두 갈래 중 하나를 고르는 일에
 *    "고르기 + 확인" 두 번을 시키면, 시트로 끝내기로 한 이유(고르고 나면 곧바로 다음이 온다)가
 *    사라진다. 그래서 쉐브론이 붙는다.
 */
function LaneStep({ onChoose }: { onChoose: (lane: PhotoLane) => void }) {
  return (
    <SheetList>
      {PHOTO_LANES.map((key) => (
        <SheetRow
          key={key}
          icon={LANE[key].icon}
          label={LANE[key].label}
          hint={LANE[key].hint}
          onClick={() => onChoose(key)}
        />
      ))}
    </SheetList>
  );
}

/* ── 2단계 · 어디서 가져오나 ──────────────────────────────────────────── */

function SourceStep({
  lane,
  recent,
  readingId,
  onBack,
  onRecent,
  onCamera,
  onAlbum,
}: {
  lane: PhotoLane;
  recent: RecentPhoto[];
  readingId: string | null;
  onBack: () => void;
  onRecent: (photo: RecentPhoto) => void;
  onCamera: () => void;
  onAlbum: () => void;
}) {
  const copy = LANE[lane];
  const busy = readingId !== null;

  return (
    <div>
      {/* 🚨 되돌아가는 길을 **고른 것 바로 옆**에 둔다. 제목이 이미 무엇을 골랐는지 말하고 있어서
          그 아래 한 줄이면 충분하다 — 시트 안에 화살표 헤더를 또 만들지 않는다.
          🚨 왼쪽 쉐브론은 장식이 아니라 **방향**이다. 시트의 다른 줄이 전부 오른쪽 쉐브론(앞으로)
          이라 왼쪽 하나가 그 짝이 된다 — 글자만 두면 1단계로 돌아간다는 것이 라벨에만 있다. */}
      <Button variant="tertiary" size="compact" disabled={busy} onClick={onBack}>
        <ChevronLeft aria-hidden size={ICON_SIZE.sm} strokeWidth={ICON_STROKE} />
        다른 종류로 바꾸기
      </Button>

      {/* 🚨 셸이 최근 사진을 주는 환경에서만 선다 (위 주석). */}
      {recent.length > 0 ? (
        <RecentStrip photos={recent} disabled={busy} readingId={readingId} onPick={onRecent} />
      ) : null}

      <SheetList className="mt-5">
        <SheetRow
          icon={Camera}
          label="사진 촬영"
          hint={copy.camera}
          disabled={busy}
          onClick={onCamera}
        />
        <SheetRow
          icon={Images}
          label="앨범에서 고르기"
          hint={copy.album}
          disabled={busy}
          onClick={onAlbum}
        />
      </SheetList>
    </div>
  );
}

/* ── 최근 사진 ────────────────────────────────────────────────────────── */

/**
 * 🚨 **가로로 민다.** 디자인 시스템 §7 이 "칩 줄은 가로 스크롤하지 않는다" 고 못박은 것은
 *    **근거 칩**이고(밀지 않으면 판단할 정보가 사라진다), 이 줄은 03 채팅바 위의 제안 줄과 같은
 *    **들어가는 문**이라 밀어도 잃는 것이 없다 — 안 민 사진은 "앨범에서 고르기" 로 그대로 간다.
 */
function RecentStrip({
  photos,
  disabled,
  readingId,
  onPick,
}: {
  photos: RecentPhoto[];
  disabled: boolean;
  readingId: string | null;
  onPick: (photo: RecentPhoto) => void;
}) {
  return (
    <section className="mt-5">
      {/* 🚨 **무엇이 있을지 넘겨짚지 않는다.** 한동안 "방금 찍은 알림장이 여기 있을 거예요" 였는데,
          기기에 무엇이 있는지 우리는 모른다 — 지킬 수 없는 약속을 라벨로 쓰지 않는다. */}
      <h3 className="text-label text-brand mb-2">최근 사진</h3>
      {/* `-mx-5 px-5` 로 시트 폭을 꽉 채운다 — 미는 줄이 여백에서 잘리면 더 있다는 것이 안 보인다. */}
      <ul className="-mx-5 flex gap-2 overflow-x-auto px-5 pb-1">
        {photos.map((photo) => {
          const reading = readingId === photo.id;
          return (
            <li key={photo.id} className="shrink-0">
              <button
                type="button"
                disabled={disabled}
                onClick={() => onPick(photo)}
                aria-busy={reading}
                className={cn(
                  "rounded-field border-line ease-standard block size-20 overflow-hidden border transition-opacity duration-120",
                  disabled ? "opacity-40" : "active:opacity-70",
                )}
              >
                {/*
                  next/image 를 쓰지 않는다 — 썸네일 주소는 셸이 주는 `data:` 라 최적화할 것도,
                  `remotePatterns` 에 적을 것도 없다.
                  🚨 `alt` 를 비워 두지 않는다. 사진 안에 무엇이 있는지는 우리가 모르므로
                     **무엇의 사진인지**만 말한다 (09 하루 패널과 같은 처리).
                */}
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img
                  src={photo.thumbnailUrl}
                  alt="기기에 있는 최근 사진"
                  className={cn("size-full object-cover", reading && "opacity-50")}
                />
              </button>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

/* ── 두 단계가 같이 쓰는 줄 목록 ──────────────────────────────────────── */

/**
 * 🚨 **줄이 시트 폭을 꽉 채운다.** `BottomSheet` 의 본문 여백(`px-5`)을 `-mx-5` 로 되돌리고
 *    같은 값을 줄 **안쪽**에 준다 — 여백을 없애는 게 아니라 **누르는 면 안으로 옮기는** 것이다.
 *    그냥 두면 눌린 면에 아이콘과 쉐브론이 붙어서 목록이 아니라 잘린 상자로 보인다.
 *    🚨 이 값은 `BottomSheet` 의 `px-5` 와 짝이다. 한쪽을 바꾸면 다른 쪽도 바꾼다.
 *
 * 🚨 **두 단계가 같은 줄 모양을 쓴다.** 1단계에서 고른 것과 2단계에서 고르는 것이 다른 종류로
 *    보이면, 넘어간 것이 아니라 다른 화면에 떨어진 것처럼 읽힌다.
 */
function SheetList({ children, className }: { children: React.ReactNode; className?: string }) {
  return <ul className={cn("border-line -mx-5 flex flex-col border-y", className)}>{children}</ul>;
}

/**
 * 시트 안의 한 줄. 🚨 **카드로 만들지 않는다** — 고르는 자리라 줄이 맞고,
 * 상자를 세우면 시트 안에서 카드 안의 카드가 된다.
 */
function SheetRow({
  icon,
  label,
  hint,
  disabled = false,
  onClick,
}: {
  icon: LucideIcon;
  label: string;
  hint: string;
  disabled?: boolean;
  onClick: () => void;
}) {
  return (
    <li className="border-line border-b last:border-b-0">
      <button
        type="button"
        disabled={disabled}
        onClick={onClick}
        // 🚨 `active:` 를 빠뜨리면 웹뷰에서 눌러도 아무 반응이 없다 (apps/web/CLAUDE.md §5).
        className={cn(
          "min-h-touch ease-standard flex w-full items-center gap-3 px-5 py-3.5 text-left transition-colors duration-120",
          disabled ? null : "hover:bg-surface-muted active:bg-surface-muted",
        )}
      >
        {/* 🚨 비활성을 브랜드색을 흐리게 만들어 표현하지 않는다 (디자인 시스템 Don't) —
            타일을 뉴트럴로 바꾼다. */}
        <IconTile icon={icon} tone={disabled ? "neutral" : "brand"} />
        <span className="min-w-0 flex-1">
          <span className={cn("text-body block", disabled ? "text-ink-muted" : "text-ink")}>
            {label}
          </span>
          <span className="text-body-sm text-ink-subtle mt-0.5 block">{hint}</span>
        </span>
        {/* 🚨 아이콘은 lucide 한 곳에서 온다 — 손으로 그린 SVG 를 섞지 않는다 (§3 아이콘). */}
        <ChevronRight
          aria-hidden
          size={ICON_SIZE.sm}
          strokeWidth={ICON_STROKE}
          className="text-ink-subtle shrink-0"
        />
      </button>
    </li>
  );
}
