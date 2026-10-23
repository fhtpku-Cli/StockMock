import { mkdir, readFile, writeFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import { resolve } from "node:path";
import { expect, webkit } from "@playwright/test";
import { launchBrowser } from "./browser.mjs";
const root = resolve(import.meta.dirname, "../..");
const mode = process.env.Q08_ONLY;
if (mode !== "before" && mode !== "after") throw new Error("请显式设置 Q08_ONLY=before 或 Q08_ONLY=after");
const output = resolve(root, ".omo/evidence/0.4.0/frontend/q08-state-reset", mode);
const url = "http://127.0.0.1:8405";
await mkdir(output, { recursive: true });
const results = [];
for (const engine of mode === "before" ? ["chromium"] : ["chromium", "webkit"]) {
 let browser;
 try {
  browser = engine === "chromium" ? await launchBrowser() : await webkit.launch({ headless: true });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, reducedMotion: "reduce" });
  const page = await context.newPage(); page.setDefaultTimeout(15000);
  const requests = []; const errors = [];
  page.on("request", request => { if (new URL(request.url()).pathname === "/api/reviews/page") requests.push(request.url()); });
  page.on("pageerror", error => errors.push(error.message));
  const controlled = await context.request.post(`${url}/_qa/control`, { data: { now: "2026-10-28T02:00:00+00:00", tick: false } }); expect(controlled.ok()).toBe(true);
  await page.goto(url); await page.getByLabel("访问密码").fill("qa-local-isolated-password");
  await page.getByRole("button", { name: "进入我的空间" }).click();
  await expect(page.getByRole("heading", { name: "资产概览" })).toBeVisible();
  const fixturePath = resolve(output, "../fixture.json");
  if (mode === "before" && !existsSync(fixturePath)) {
   const seeded = await context.request.post(`${url}/_qa/seed-reviews`, { data: { plans: 36, notes: 0 } }); expect(seeded.ok()).toBe(true);
   await writeFile(fixturePath, JSON.stringify(await seeded.json(), null, 2));
  }
  const fixture = JSON.parse(await readFile(fixturePath, "utf8"));
  await page.getByRole("button", { name: "复盘", exact: true }).click();
  const workbench = page.locator("section.panel").filter({ has: page.getByRole("heading", { name: "计划与交易复盘", exact: true }) });
  await page.getByLabel("复盘起始日期").fill("2026-10-28"); await page.getByLabel("复盘结束日期").fill("2026-10-28");
  await page.getByRole("button", { name: "应用复盘筛选" }).click();
  const state = page.getByLabel("复盘状态", { exact: true }); const cards = workbench.locator(".review-card");
  await state.selectOption("all"); await expect(cards).toHaveCount(25);
  await workbench.getByRole("button", { name: "加载更多", exact: true }).click(); await expect(cards).toHaveCount(36);
  for (const width of mode === "before" ? [1440] : [1440, 390, 320]) {
   await page.setViewportSize({ width, height: width === 1440 ? 1000 : 844 });
   for (const entrance of ["select", "count-button"]) {
    const startedAt = Date.now(); const start = requests.length;
    if (entrance === "select") await state.selectOption("pending");
    else await workbench.getByRole("button", { name: "待首次复盘 · 18", exact: true }).click();
    await expect(cards).toHaveCount(18);
    const pendingRequests = requests.slice(start); const backStart = requests.length;
    await state.selectOption("all"); await expect(cards).toHaveCount(mode === "before" ? 36 : 25);
    const backRequests = requests.slice(backStart); const elapsedMs = Date.now() - startedAt;
    expect(elapsedMs).toBeLessThan(20000);
    if (mode === "before") expect(backRequests).toHaveLength(0);
    else {
     expect(pendingRequests).toHaveLength(1); expect(backRequests).toHaveLength(1);
     for (const request of [...pendingRequests, ...backRequests]) expect(new URL(request).searchParams.has("cursor")).toBe(false);
    }
    const firstPageCount = await cards.count();
    await workbench.getByRole("heading", { name: "计划与交易复盘", exact: true }).scrollIntoViewIfNeeded();
    const artifact = resolve(output, `${engine}-${entrance}-${width}.png`); await page.screenshot({ path: artifact });
    if (mode !== "before") { await workbench.getByRole("button", { name: "加载更多", exact: true }).click(); await expect(cards).toHaveCount(36); }
    const ids = await cards.evaluateAll(items => items.map(item => item.dataset.reviewId));
    expect(new Set(ids).size).toBe(36); expect([...ids].sort()).toEqual(fixture.plans.map(plan => plan.id).sort());
    const overflow = await page.evaluate(() => [...document.querySelectorAll(".main-scroll")].some(el => el.scrollWidth > el.clientWidth + 1)); expect(overflow).toBe(false);
    results.push({ engine, entrance, width, expectedDefect: mode === "before", passed: true, elapsedMs, firstPageCount, pendingRequests, backRequests, finalCount: ids.length, unique: new Set(ids).size, overflow, artifact, steps: ["selectOption('all'); click('加载更多'): 25→36", entrance === "select" ? "getByLabel('复盘状态').selectOption('pending')" : "getByRole('button',{name:'待首次复盘 · 18',exact:true}).click()", "getByLabel('复盘状态').selectOption('all')", "断言首批记录数和新HTTP请求；修复后click('加载更多')再到36且ID完整无重复"] });
    await writeFile(resolve(output, "observations.json"), JSON.stringify(results, null, 2));
   }
  }
  expect(errors).toEqual([]);
 } finally { if (browser) await browser.close(); }
}
console.log(JSON.stringify({ mode, passed: true, observations: results.length, browsersClosed: true, output }));
