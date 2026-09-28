"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { AuthGate } from "@/components/auth-gate";
import { ConsentRequiredCard } from "@/components/consent-required-card";
import { Button } from "@/components/ui/button";
import { Card, CardFailed } from "@/components/ui/card";
import { SafetySection } from "@/components/safety-section";
import { Chip, ChipRow } from "@/components/ui/chip";
import { ChoiceField } from "@/components/ui/choice-field";
import { PageTitle } from "@/components/ui/page-title";
import { Screen } from "@/components/ui/screen";
import { Section } from "@/components/ui/section";
import { Spinner } from "@/components/ui/spinner";
import { TextInput } from "@/components/ui/text-input";
import { useChildId } from "@/hooks/use-child-id";
import {
  isApiError,
  qk,
  submitOnboarding,
  type Gender,
  type OnboardingRequest,
  type Relation,
} from "@/lib/api";
import { useIdempotencyKey } from "@/lib/api/use-idempotency-key";
import { DEFAULT_GENDER, GENDER_OPTIONS } from "@/lib/gender";
import { parseMeasurement } from "@/lib/measurement";
import { OWNER_RELATIONS, relationOptions } from "@/lib/relation";

/**
 * 02 아이 정보 — **전부 선택이다.** 모두 건너뛰어도 200 이고 홈으로 간다.
 *
 * 🚨 **01 이 받지 않는 것을 전부 여기서 받는다.** 01 은 되돌리기 어려운 것(아이를 만드는 일 ·
 *    법정대리인 동의)만 받고, 고를 수 있는 것은 이 화면이 진다.
 *
 * 🚨 **11 아이 프로필과 같은 물건으로 묻는다** — 성별은 같은 `ChoiceField` 와 같은 목록
 *    (`lib/gender.ts`), 키·몸무게는 같은 읽기 규칙(`lib/measurement.ts`)이다. 두 화면이
 *    각자 폼을 들면 한쪽에만 붙은 검증이 생기고, 실제로 그렇게 갈렸던 적이 있다
 *    (11 과 11-1 이 `GrowthSheet` 하나를 나눠 쓰는 것과 같은 판단).
 *    🚨 다만 **시트를 그대로 가져오지는 않았다.** 11 의 시트들은 열릴 때마다 각자 저장하는
 *       물건이라(`PATCH /children/{cid}` · `POST /growth` · 승인 게이트 ㉡), 이 화면에
 *       올리면 "다음" 을 누르기도 전에 세 번 저장된다 — 건너뛰기가 뜻을 잃는다.
 *       여기서는 한 번에 모아 `POST /children/{cid}/onboarding` 하나로 보낸다.
 *
 * 🚨 **관심사와 발달 문항을 뺐다.** 관심은 03 홈의 한 줄에서 관찰로 쌓이는 값이고, 첫날
 *    칩으로 고른 여덟 개는 보호자가 짐작한 목록이다. 발달 문항은 화면에 "발달 상태" 라는
 *    말이 서는 순간 발달 평가로 읽힌다 — 안 만들기로 한 것이다 (최상위 §1 · §2).
 *
 * 🚨 여기서 고른 값은 전부 **보호자 자기보고**다. AI 가 추론한 값을 여기에 섞지 않는다.
 */

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

  const [relation, setRelation] = useState<Relation | null>(null);
  /**
   * 🚨 **출발값이 "밝히지 않을래요" 다** (최상위 §2). `male` 을 먼저 세워 두지 않는다.
   */
  const [gender, setGender] = useState<Gender>(DEFAULT_GENDER);
  const [height, setHeight] = useState("");
  const [weight, setWeight] = useState("");
  const [measureErrors, setMeasureErrors] = useState<{ height?: string; weight?: string }>({});
  const [emptyNotice, setEmptyNotice] = useState(false);

  /**
   * 🚨 **재시도할 때 키를 새로 만들지 않는다** — 같은 키를 다시 보내는 게 중복 저장을 막는
   *    유일한 방법이다. `mutationFn` 안에서 키를 만들면 재시도마다 새 키가 나간다
   *    (`use-idempotency-key.ts`).
   */
  const idempotencyKey = useIdempotencyKey();

  const save = useMutation({
    mutationFn: (body: OnboardingRequest) =>
      submitOnboarding(childId, body, idempotencyKey.current()),
    onSuccess: async () => {
      // 온보딩 한 번이 프로필·안전정보·홈을 동시에 바꾼다. 아이 스코프를 통째로 무효화한다.
      await queryClient.invalidateQueries({ queryKey: qk.child(childId) });
      router.replace(`/child/${childId}/home`);
    },
  });

  function buildBody(): OnboardingRequest | null {
    const heightValue = parseMeasurement(height);
    const weightValue = parseMeasurement(weight);

    const body: OnboardingRequest = {
      ...(relation ? { relation } : {}),
      // 🚨 **기본값은 보내지 않는다.** 서버의 출발값과 같은 값이라 보낼 것이 없고, 보내면
      //    아래 "아무것도 적지 않음" 검사가 늘 통과해 안내가 영영 안 뜬다.
      ...(gender !== DEFAULT_GENDER ? { gender } : {}),
      ...(typeof heightValue === "number" ? { height_cm: heightValue } : {}),
      ...(typeof weightValue === "number" ? { weight_kg: weightValue } : {}),
    };

    return Object.keys(body).length > 0 ? body : null;
  }

  // 하나라도 채우면 안내가 저절로 사라지도록 렌더 때마다 다시 만든다.
  const body = buildBody();

  function submit() {
    /**
     * 🚨 **잘못 적은 숫자를 조용히 버리지 않는다.** `buildBody()` 는 숫자가 아닌 값을 그냥
     *    빼는데, 그대로 제출하면 **적은 사람만 적었다고 믿는다.** 여기서 먼저 막는다.
     */
    const next: { height?: string; weight?: string } = {};
    if (parseMeasurement(height) === "invalid") next.height = "숫자로 적어주세요.";
    if (parseMeasurement(weight) === "invalid") next.weight = "숫자로 적어주세요.";
    setMeasureErrors(next);
    if (Object.keys(next).length > 0) return;

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
        <p className="text-label text-ink-subtle">아이 등록 · 2 / 2</p>
        <PageTitle className="mt-2">
          알려주실 게
          <br />
          있다면 지금
        </PageTitle>
        <p className="text-body text-ink-muted mt-3">
          전부 선택이에요. 편한 것만 적고 넘어가도 되고, 나중에 프로필에서 적어도 돼요.
        </p>
      </div>

      <Section title="아이와의 관계" description="10 설정의 보호자 목록에 이렇게 보여요.">
        {/* 🚨 고를 수 있는 것이 **셋뿐이다.** 01 에서 법정대리인 동의를 한 사람이라
            시터·그 밖에는 여기 설 수 없다 (`lib/relation.ts` 의 `OWNER_RELATIONS`).
            함께 보는 시터는 초대로 들어오고, 그 화면은 5종 전부다. */}
        <ChipRow>
          {relationOptions(OWNER_RELATIONS).map((item) => (
            <Chip
              key={item.value}
              selected={relation === item.value}
              onClick={() => setRelation(relation === item.value ? null : item.value)}
            >
              {item.label}
            </Chip>
          ))}
        </ChipRow>
      </Section>

      {/*
        🚨 **11 프로필과 같은 물건·같은 목록이다** (`ChoiceField` · `lib/gender.ts`).
           "밝히지 않을래요" 가 기본값이라 이 칸은 **비어 있을 수 없는 값**이고, 그래서
           칩(둘 다 꺼진 상태가 정상)이 아니라 라디오다 (디자인 시스템 §7 고르는 칸).
        🚨 **이것만 `Section` 으로 감싸지 않는다.** 컨트롤이 하나뿐인데 구역 제목까지 세우면
           제목과 `<legend>` 가 **같은 말을 두 번** 한다 ("성별" 아래 "성별"). 이름은 legend 가
           져야 한다 — 구역 제목은 `<fieldset>` 과 연결되지 않아서 보조기술에 안 붙는다.
      */}
      <ChoiceField
        label="성별"
        hint="나이에 맞는 제안을 고를 때만 써요. 밝히지 않아도 돼요."
        value={gender}
        options={GENDER_OPTIONS}
        onChange={setGender}
      />

      <Section title="키 · 몸무게" description="오늘 잰 값으로 기록해요. 한쪽만 적어도 돼요.">
        {/* 🚨 **잰 날짜를 여기서 묻지 않는다.** 서버가 오늘로 찍는다 — 프론트가 날짜를
            만들지 않는다 (CLAUDE.md §4). 다른 날 잰 값은 11-1 에서 날짜를 골라 적는다.
            🚨 백분위·또래 비교·"빠르다/느리다" 를 여기에도 만들지 않는다 (최상위 §2). */}
        <div className="flex gap-3">
          <div className="flex-1">
            <TextInput
              label="키 (cm)"
              inputMode="decimal"
              autoComplete="off"
              placeholder="예: 104.2"
              value={height}
              onChange={(e) => setHeight(e.target.value)}
              error={measureErrors.height}
            />
          </div>
          <div className="flex-1">
            <TextInput
              label="몸무게 (kg)"
              inputMode="decimal"
              autoComplete="off"
              placeholder="예: 17.1"
              value={weight}
              onChange={(e) => setWeight(e.target.value)}
              error={measureErrors.weight}
            />
          </div>
        </div>
      </Section>

      {/*
        🚨 **11 아이 프로필과 같은 구역이다** (`components/safety-section.tsx`) — 직접 적기와
           검사지 사진, 두 길이 그대로 열린다.
        🚨 **이 구역만 다른 버튼을 따라간다.** 나머지 셋은 아래 "다음" 이 한 번에 보내지만,
           알레르기는 **승인 게이트 ㉡** 라 시트에서 확정하는 즉시 저장된다 (NF-03 —
           알레르기의 유일한 쓰기 경로). 그래서 여기서 등록한 것은 "건너뛰기" 를 눌러도 남는다.
      */}
      <SafetySection childId={childId} />

      <div className="mt-auto flex flex-col gap-3 pt-4">
        {emptyNotice && !body ? (
          <Card>
            <p className="text-body-sm text-ink-muted">
              아직 아무것도 적지 않으셨어요. 하나만 골라주거나 건너뛰기를 눌러주세요.
            </p>
          </Card>
        ) : null}

        {consentBlocked ? (
          <ConsentRequiredCard
            childId={childId}
            error={consentBlocked}
            what="적어주신 것을 저장할 수 없어요."
          />
        ) : save.isError ? (
          <CardFailed>
            <p>{save.error instanceof Error ? save.error.message : "저장하지 못했어요."}</p>
            <Button variant="tertiary" size="compact" className="mt-3" onClick={submit}>
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
          적은 값만 저장하고, AI 가 추론한 값은 저장하지 않아요.
          <br />
          건너뛰어도 나중에 프로필에서 적을 수 있어요.
        </p>
      </div>
    </Screen>
  );
}
