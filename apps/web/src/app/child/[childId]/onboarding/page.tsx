"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useRef, useState, type ReactNode } from "react";

import { AuthGate } from "@/components/auth-gate";
import { Button } from "@/components/ui/button";
import { Card, CardFailed } from "@/components/ui/card";
import { Chip, ChipRow } from "@/components/ui/chip";
import { PageTitle } from "@/components/ui/page-title";
import { Screen } from "@/components/ui/screen";
import { Spinner } from "@/components/ui/spinner";
import { TextInput } from "@/components/ui/text-input";
import { useChildId } from "@/hooks/use-child-id";
import {
  api,
  isApiError,
  newIdempotencyKey,
  qk,
  type DevScreeningResponse,
  type OnboardingRequest,
  type OnboardingResponse,
  type SafetyStatus,
} from "@/lib/api";

/**
 * 02 이야기 하나 — 전부 선택이다. 모두 건너뛰어도 200 이고 홈으로 간다.
 *
 * 프로토타입의 ⑤ 캘린더 연동 토글은 뺐다 — 요청 본문에 대응하는 필드가 없고,
 * 연동 자체가 아직 열려 있는 결정이다 (CLAUDE.md §10).
 *
 * 🚨 여기서 고른 값은 전부 **보호자 자기보고**다. AI 가 추론한 값을 여기에 섞지 않는다.
 */

const INTERESTS = [
  "공룡",
  "자동차",
  "블록",
  "그림 그리기",
  "물놀이",
  "공놀이",
  "동물",
  "노래 부르기",
];

const SAFETY_CHOICES: Array<{ value: SafetyStatus; label: string }> = [
  { value: "none", label: "없음" },
  { value: "has", label: "있음 · 입력" },
  { value: "unknown", label: "잘 모르겠어요" },
];

export default function ChildOnboardingPage() {
  return (
    <AuthGate>
      <ChildOnboardingScreen />
    </AuthGate>
  );
}

function ChildOnboardingScreen() {
  const childId = useChildId();
  const router = useRouter();
  const queryClient = useQueryClient();

  const [interests, setInterests] = useState<string[]>([]);
  const [safetyStatus, setSafetyStatus] = useState<SafetyStatus | null>(null);
  const [safetyText, setSafetyText] = useState("");
  const [oneLine, setOneLine] = useState("");
  const [devAnswers, setDevAnswers] = useState<Record<string, number>>({});
  const [emptyNotice, setEmptyNotice] = useState(false);

  const screening = useQuery({
    queryKey: qk.devScreening(childId),
    // 🚨 계약서의 `?age_months=` 는 프론트가 만들 수 없는 값이다 — 생일에서 개월 수를 계산해야 한다.
    //    서버가 birth_date 를 갖고 있으니 child_id 를 보내고 환산은 서버가 한다 (#18 에서 확정).
    queryFn: () =>
      api.get<DevScreeningResponse>("/dev-screening/items", { query: { child_id: childId } }),
  });

  /** 🚨 재시도할 때 키를 새로 만들지 않는다 — 같은 키를 다시 보내는 게 중복 저장을 막는 유일한 방법이다. */
  const idempotencyKey = useRef<string | null>(null);

  const save = useMutation({
    mutationFn: (body: OnboardingRequest) => {
      idempotencyKey.current ??= newIdempotencyKey();
      return api.post<OnboardingResponse>(`/children/${childId}/onboarding`, body, {
        idempotencyKey: idempotencyKey.current,
      });
    },
    onSuccess: async () => {
      // 온보딩 한 번이 관찰·프로필·홈을 동시에 바꾼다. 아이 스코프를 통째로 무효화한다.
      await queryClient.invalidateQueries({ queryKey: qk.child(childId) });
      router.replace(`/child/${childId}/home`);
    },
  });

  function buildBody(): OnboardingRequest | null {
    const safetyLabels =
      safetyStatus === "has"
        ? safetyText
            .split(",")
            .map((s) => s.trim())
            .filter(Boolean)
        : [];

    const body: OnboardingRequest = {
      ...(interests.length > 0 ? { interests } : {}),
      ...(safetyStatus ? { safety_status: safetyStatus } : {}),
      // 🚨 보호자가 적은 라벨만 그대로 올린다. 심각도·반응은 여기서 추측하지 않는다 (NF-03).
      ...(safetyLabels.length > 0
        ? {
            safety: safetyLabels.map((label) => ({
              type: "allergy",
              label,
              category: "식품",
            })),
          }
        : {}),
      ...(oneLine.trim() ? { one_line: oneLine.trim() } : {}),
      ...(Object.keys(devAnswers).length > 0
        ? {
            dev_answers: Object.entries(devAnswers).map(([item_id, level]) => ({ item_id, level })),
          }
        : {}),
    };

    return Object.keys(body).length > 0 ? body : null;
  }

  // 하나라도 채우면 안내가 저절로 사라지도록 렌더 때마다 다시 만든다.
  const body = buildBody();

  function submit() {
    if (!body) {
      setEmptyNotice(true);
      return;
    }
    setEmptyNotice(false);
    save.mutate(body);
  }

  const consentBlocked = isApiError(save.error, "consent_required") ? save.error : null;

  return (
    <Screen className="gap-5">
      <div>
        <p className="text-label text-ink-subtle">이야기 하나 · 2 / 2</p>
        <PageTitle className="mt-2">
          아이 이야기,
          <br />
          하나만 들려주세요
        </PageTitle>
        <p className="text-body text-ink-muted mt-3">
          편한 것 하나만 해도 돼요. 전부 건너뛰어도 괜찮아요.
        </p>
      </div>

      <Section title="① 요즘 좋아하는 것" note="여러 개 골라도 되고, 안 골라도 돼요.">
        <ChipRow>
          {INTERESTS.map((item) => (
            <Chip
              key={item}
              selected={interests.includes(item)}
              onClick={() =>
                setInterests((prev) =>
                  prev.includes(item) ? prev.filter((v) => v !== item) : [...prev, item],
                )
              }
            >
              {item}
            </Chip>
          ))}
        </ChipRow>
        <p className="text-caption text-ink-subtle mt-2">고른 것은 보호자 자기보고로 저장돼요.</p>
      </Section>

      <Section title="② 알레르기 · 식품 제한" note="식사 제안을 걸러내는 데만 써요.">
        <ChipRow>
          {SAFETY_CHOICES.map((choice) => (
            <Chip
              key={choice.value}
              selected={safetyStatus === choice.value}
              onClick={() => setSafetyStatus(safetyStatus === choice.value ? null : choice.value)}
            >
              {choice.label}
            </Chip>
          ))}
        </ChipRow>

        {safetyStatus === "has" ? (
          <div className="mt-3">
            <TextInput
              label="어떤 것을 피해야 하나요"
              hint="쉼표로 구분해 적어주세요."
              placeholder="예: 우유, 땅콩"
              value={safetyText}
              onChange={(e) => setSafetyText(e.target.value)}
            />
          </div>
        ) : null}

        {safetyStatus === "unknown" ? (
          <p className="text-caption text-ink-subtle mt-3">
            지금은 저장하지 않을게요. 확인되기 전까지 식사 제안은 만들지 않아요.
          </p>
        ) : null}

        <p className="text-caption text-ink-subtle mt-2">
          보호자가 입력한 값만 저장돼요. AI 가 추측한 알레르기 정보는 저장하지 않아요.
        </p>
      </Section>

      <Section title="③ 오늘 있었던 일 한 줄">
        <TextInput
          label="말하듯 적어주세요"
          placeholder="예: 오늘은 블록을 오래 쌓았어요"
          value={oneLine}
          onChange={(e) => setOneLine(e.target.value)}
          maxLength={200}
        />
      </Section>

      {screening.data && screening.data.items.length > 0 ? (
        <Section
          title="④ 요즘 발달 상태 · 보호자가 보기에"
          note={`${screening.data.age_band} 선별 문항이에요. 각 문항에 맞는 수준을 골라주세요.`}
        >
          <div className="flex flex-col gap-4">
            {screening.data.items.map((item) => (
              <div key={item.item_id}>
                <p className="text-body-sm text-ink">{item.text}</p>
                <ChipRow>
                  {item.levels.map((level) => (
                    <Chip
                      key={level.level}
                      selected={devAnswers[item.item_id] === level.level}
                      onClick={() =>
                        setDevAnswers((prev) => {
                          if (prev[item.item_id] !== level.level) {
                            return { ...prev, [item.item_id]: level.level };
                          }
                          const next = { ...prev };
                          delete next[item.item_id];
                          return next;
                        })
                      }
                    >
                      {level.label}
                    </Chip>
                  ))}
                </ChipRow>
              </div>
            ))}
          </div>
          <p className="text-caption text-ink-subtle mt-2">
            발달 검사나 진단이 아니에요. 보호자가 고른 값만 저장하고, AI 는 발달을 평가하지 않아요.
          </p>
        </Section>
      ) : null}

      <div className="mt-auto flex flex-col gap-3 pt-4">
        {emptyNotice && !body ? (
          <Card>
            <p className="text-body-sm text-ink-muted">
              아직 아무것도 들려주지 않으셨어요. 하나만 골라주거나 건너뛰기를 눌러주세요.
            </p>
          </Card>
        ) : null}

        {consentBlocked ? (
          <Card>
            <p className="text-body text-ink">먼저 동의가 필요해요</p>
            <p className="text-body-sm text-ink-muted mt-2">
              저장은 아직 하나도 되지 않았어요. 동의를 마치면 그대로 다시 보낼 수 있어요.
            </p>
            <p className="text-caption text-ink-subtle mt-2">
              동의 화면은 아직 없어요 — /child/{childId}/{consentBlocked.consentDeeplink}
            </p>
          </Card>
        ) : save.isError ? (
          <CardFailed>
            <p>{save.error instanceof Error ? save.error.message : "저장하지 못했어요."}</p>
            <Button variant="tertiary" className="mt-1 -ml-2" onClick={submit}>
              다시 시도
            </Button>
          </CardFailed>
        ) : null}

        <Button block onClick={submit} disabled={save.isPending}>
          {save.isPending ? <Spinner /> : null}
          {save.isPending ? "저장하는 중…" : "다음"}
        </Button>
        <Button
          variant="secondary"
          block
          onClick={() => router.replace(`/child/${childId}/home`)}
          disabled={save.isPending}
        >
          지금은 건너뛰기
        </Button>
        <p className="text-caption text-ink-subtle text-center">
          고른 값만 저장하고, AI 가 추론한 값은 저장하지 않아요.
          <br />
          건너뛰면 제안 없이 홈으로 가요.
        </p>
      </div>
    </Screen>
  );
}

function Section({ title, note, children }: { title: string; note?: string; children: ReactNode }) {
  return (
    <section className="border-line rounded-card bg-surface border p-4">
      <h2 className="text-section text-ink">{title}</h2>
      {note ? <p className="text-body-sm text-ink-muted mt-1">{note}</p> : null}
      <div className="mt-3">{children}</div>
    </section>
  );
}
