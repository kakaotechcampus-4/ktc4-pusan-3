// 실서버 화면을 열어 화면 투어를 끝까지 넘기며 단계마다 스크린샷을 남긴다.
//
//   cd apps/web && node ../../.claude/skills/screen-tour/walk.mjs <state.json> <outdir> <이름>@<경로>[@help] ...
//
// - `@help` 를 붙이면 저절로 뜨기를 기다리지 않고 ? 를 눌러 연다 (이미 본 화면 · 10 설정)
// - 화면마다 단계 번호 · 제목을 찍고 `<outdir>/<이름>-<n>.png` 를 남긴다. 레이아웃은 그 그림으로 본다
// - 🚨 apps/web 에서 돌린다 — @playwright/test 를 지금 폴더 기준으로 찾는다 (이 파일 옆에는 node_modules 가 없다)
import { createRequire } from "node:module";

const { chromium } = createRequire(`${process.cwd()}/`)("@playwright/test");

const [state, outdir, ...jobs] = process.argv.slice(2);
const base = process.env.TOUR_BASE_URL ?? "http://localhost:3000";
const browser = await chromium.launch();
const ctx = await browser.newContext({ storageState: state, viewport: { width: 390, height: 844 } });
const page = await ctx.newPage();
const problems = [];
page.on("console", (m) => m.type() === "error" && problems.push(`console ${m.text().slice(0, 300)}`));
page.on("pageerror", (e) => problems.push(`pageerror ${e.message.slice(0, 300)}`));

for (const job of jobs) {
  const [name, path, mode] = job.split("@");
  await page.goto(base + path, { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(3500);
  if (mode === "help") {
    await page.getByRole("button", { name: "이 화면 설명 보기" }).click();
    await page.waitForTimeout(500);
  }
  console.log(`== ${name} ${path}`);
  for (let i = 0; i < 9; i++) {
    const tour = page.locator("dialog[open][aria-describedby]");
    if (!(await tour.count())) {
      await page.screenshot({ path: `${outdir}/${name}-${i}.png` });
      console.log(i, "투어 없음");
      break;
    }
    await page.waitForTimeout(250);
    await page.screenshot({ path: `${outdir}/${name}-${i}.png` });
    const count = (await tour.locator("p").first().innerText()).split("\n")[0];
    console.log(i, count, "|", await tour.locator("h2").innerText());
    const next = tour.getByRole("button", { name: /다음|다 봤어요/ });
    const label = await next.innerText();
    await next.click();
    if (label.includes("다 봤어요")) break;
  }
}

await ctx.storageState({ path: state });
if (problems.length) console.log(problems.join("\n"));
await browser.close();
