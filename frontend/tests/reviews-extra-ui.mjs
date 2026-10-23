import { randomUUID } from "node:crypto";
import { readFile, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { expect, webkit } from "@playwright/test";
import { launchBrowser } from "./browser.mjs";
const output = process.env.REVIEWS_EVIDENCE_DIR ?? resolve(import.meta.dirname, "../../.omo/evidence/0.4.0/frontend");
const url = "http://127.0.0.1:8405";
const observations = JSON.parse(await readFile(resolve(output, "main-observations.json"), "utf8"));
let fixture;
for (const engine of ["chromium", "webkit"]) {
 let browser, page;
 try {
  browser = engine === "chromium" ? await launchBrowser() : await webkit.launch({ headless: true });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, reducedMotion: "reduce" });
  page = await context.newPage(); page.setDefaultTimeout(15000);
  const request = async (path, data, method = "POST") => { const response = await context.request.fetch(url + path, { method: data === undefined ? "GET" : method, ...(data === undefined ? {} : { data, headers: { Origin: url } }) }); expect(response.ok(), `${path}: ${await response.text()}`).toBe(true); return response.json(); };
  const api = (path, data, method) => request(`/api/${path}`, data, method);
  const control = (data) => request("/_qa/control", data);
  const capture = async (name, steps, observable = {}) => { const artifact = resolve(output, `${engine}-${name}.png`); await page.screenshot({ path: artifact }); const overflow = await page.evaluate(() => [...document.querySelectorAll(".modal[open],.main-scroll")].filter(e => e.getClientRects().length && e.scrollWidth > e.clientWidth + 1).map(e => e.className)); expect(overflow).toEqual([]); observations.push({ engine, name, steps, observable, artifact, passed: true }); };
  await page.goto(url); await page.getByLabel("访问密码").fill("qa-local-isolated-password"); await page.getByRole("button", { name: "进入我的空间" }).click(); await expect(page.getByRole("heading", { name: "资产概览" })).toBeVisible();
  if (!fixture) {
   await control({ now: "2026-10-23T02:00:00+00:00", tick: false });
   await api("auth/login", { password: "qa-local-isolated-password" });
   for (const order of await api("orders")) if (order.status === "accepted") await api(`orders/${order.id}/cancel`, {});
   await control({ minutes: 2 });
   await api("rounds/reset", { request_id: randomUUID(), confirmation: "重新开始" });
   await control({ now: "2026-10-22T02:00:00+00:00", price: "10", tick: false });
   let plan = await api("plans", { symbol: "510300.SS", direction: "buy", status: "active", reason: "早期原计划：等待估值", target_price: "10", stop_price: "8", review_date: "2026-10-22", invalidation: "不自动买卖" });
   await api("plans", { symbol: "510300.SS", direction: "watch", status: "draft", reason: "同证券另一条计划，不应误开", target_price: "10", stop_price: null, review_date: "2026-10-22", invalidation: "" });
   await control({ minutes: 2 });
   const placed = await api("orders", { request_id: randomUUID(), symbol: plan.symbol, side: "buy", order_type: "limit", quantity: 200, limit_price: "10", reason: "第一版下单依据", plan_id: plan.id, plan_version: plan.version });
   await control({ minutes: 2 });
   await control({ now: "2026-10-23T02:00:00+00:00", tick: false });
   await api("auth/login", { password: "qa-local-isolated-password" });
   plan = await api(`plans/${plan.id}`, { symbol: plan.symbol, direction: plan.direction, status: plan.status, reason: "当前第二版：追加核对，不冒充创建时全文", target_price: "11", stop_price: "8", review_date: plan.review_date, invalidation: plan.invalidation, version: plan.version }, "PUT");
   await control({ price: "12", tick: false });
   await api("orders", { request_id: randomUUID(), symbol: plan.symbol, side: "sell", order_type: "limit", quantity: 100, limit_price: "12", reason: "次交易日部分卖出", plan_id: plan.id, plan_version: plan.version });
   await control({ minutes: 2, price: "12" });
   const actual = await api(`reviews/plan/${plan.id}?round_id=${plan.round_id}`); expect(actual.review.realized_pnl).toBe("198.90"); expect(actual.review.remaining_quantity).toBe(100); expect(actual.review.account_quantity).toBe(100);
   const seed = await request("/_qa/seed-reviews", { plans: 36, notes: 525 });
   const legacyId = randomUUID();
   await request("/_qa/seed-orders", { orders: [{ id: legacyId, request_id: randomUUID(), symbol: "MSFT", side: "sell", order_type: "limit", quantity: 100, limit_price: "10", price_bound: "10", status: "filled", created_at: "2026-10-23T02:00:00+00:00", active_at: "2026-10-23T02:00:00+00:00", expires_at: "2026-10-24T02:00:00+00:00", filled_at: "2026-10-23T01:59:00+00:00", confirmed_at: null, fill_price: "10", fee: "1", currency: "USD", source: "受控旧委托", reason: "=SUM(1,2)\n中文,旧委托", realized_pnl: "-12.50", cost_basis: "1011.50" }] });
   await api("journal", { plan_id: plan.id, text: "仅关联计划的中文笔记，保留市场" });
   fixture = { roundId: seed.round_id, planId: plan.id, legacyId, placedId: placed.id, longPlanId: seed.plans[0].id };
   await writeFile(resolve(output, "pm-fixture.json"), JSON.stringify(fixture, null, 2));
  }
  await page.reload(); await page.getByRole("button", { name: "复盘", exact: true }).click(); await page.getByLabel("复盘状态", { exact: true }).selectOption("all");
  const workbench = page.locator("section.panel").filter({ has: page.getByRole("heading", { name: "计划与交易复盘", exact: true }) }); await expect(workbench.locator(".review-card")).toHaveCount(25); await expect(workbench.locator(`[data-review-id="${fixture.planId}"]`)).toHaveCount(0);
  await page.getByRole("button", { name: "资产", exact: true }).click(); await page.getByRole("button", { name: "全部提示", exact: true }).click();
  const alertPage = await api("alerts?unread_only=false&limit=5"); const alertIndex = alertPage.items.findIndex(item => item.plan_id === fixture.planId && item.plan_version === 1); expect(alertIndex).toBeGreaterThanOrEqual(0);
  const alert = page.locator(".alert-list article").nth(alertIndex); await alert.getByRole("button", { name: "查看相关计划/复盘" }).click(); const modal = page.getByRole("dialog", { name: "相关计划与复盘" });
  await expect(modal.locator(".plan-summary").first()).toContainText("当前第二版"); await expect(modal).toContainText("198.90"); await expect(modal.locator(".plan-summary").first()).not.toContainText("同证券另一条计划"); await expect(modal.getByRole("heading", { name: "原始提示背景 · 计划第 1 版" })).toBeVisible();
  const created = modal.locator(".review-facts article").filter({ has: page.getByText("计划创建", { exact: true }) }); await expect(created).toBeVisible(); await expect(created).not.toContainText("当前第二版"); await expect(created).not.toContainText("计划第 2 版"); await created.scrollIntoViewIfNeeded(); await capture("creation-fact-no-invented-history", ["open alert for subject absent from first page", "inspect plan_created fact"], { originalVersion: 1, currentVersion: 2, createdFactHasCurrentText: false });
  await modal.getByRole("button", { name: "返回来处" }).click(); await expect(alert).toContainText("未读");
  await page.route(`**/api/reviews/plan/${fixture.planId}?*`, route => route.fulfill({ status: 404, json: { detail: { code: "review_not_found", message: "目标计划不存在" } } })); await alert.getByRole("button", { name: "查看相关计划/复盘" }).click(); await expect(modal.getByRole("alert")).toContainText("目标计划不存在"); await expect(modal.locator(".plan-summary")).toHaveCount(0); await capture("missing-target-no-fallback", ["return 404 for exact subject", "click same alert"], { openedOtherPlan: false }); await modal.getByRole("button", { name: "返回来处" }).click(); await page.unroute(`**/api/reviews/plan/${fixture.planId}?*`);
  await page.getByRole("button", { name: "自选", exact: true }).click(); await page.getByLabel("筛选已可交易证券").fill("510300"); await page.locator('[data-symbol="510300.SS"] .security-main').first().click(); const security = page.getByRole("dialog").filter({ has: page.getByRole("heading", { name: "我的交易计划", exact: true }) });
  await security.getByRole("button", { name: "全部提示", exact: true }).click(); const symbolAlerts = await api("alerts?symbol=510300.SS&unread_only=false&limit=10"); const symbolIndex = symbolAlerts.items.findIndex(item => item.plan_id === fixture.planId && item.plan_version === 1); await security.locator(".alert-list article").nth(symbolIndex).getByRole("button", { name: "查看相关计划/复盘" }).click(); await expect(modal.locator(".plan-summary").first()).toContainText("当前第二版"); await capture("security-alert-direct", ["open security", "click exact plan alert"], { exactPlan: fixture.planId }); await modal.getByRole("button", { name: "返回来处" }).click(); await expect(security.getByRole("heading", { name: "我的交易计划", exact: true })).toBeVisible(); await security.getByRole("button", { name: "关闭", exact: true }).click();
  await page.getByRole("button", { name: "复盘", exact: true }).click(); await page.getByLabel("复盘状态", { exact: true }).selectOption("all"); await page.getByLabel("复盘证券代码").fill("MSFT"); await page.getByRole("button", { name: "应用复盘筛选" }).click(); const card = workbench.locator(`[data-review-id="${fixture.legacyId}"]`); await card.getByRole("button", { name: "查看计划与实际结果" }).click(); await expect(card).toContainText("-100"); await expect(card).toContainText("12.50"); await expect(card).toContainText("未保存当时计划"); await expect(card.getByRole("heading", { name: "时间未完整记录", exact: true })).toBeVisible();
  for (const width of [1440,390,320]) { await page.setViewportSize({ width, height: width === 1440 ? 1000 : 844 }); await card.locator(".review-facts").scrollIntoViewIfNeeded(); await capture(`unknown-time-legacy-${width}`, ["filter MSFT", "open legacy order", "scroll facts", "setViewportSize"], { confirmationTimeUnknown: true, netShares: -100, pnl: "-12.50" }); await workbench.getByRole("heading", { name: "计划与交易复盘" }).scrollIntoViewIfNeeded(); await capture(`workbench-filter-${width}`, ["scroll workbench filters"]); }
  const downloading = page.waitForEvent("download"); await page.getByRole("button", { name: "导出筛选复盘", exact: true }).click(); const download = await downloading; const csvPath = resolve(output, `${engine}-legacy-negative.csv`); await download.saveAs(csvPath); const csv = await readFile(csvPath,"utf8"); expect(csv).toContain("-12.50"); expect(csv).not.toContain("'-12.50"); expect(csv).toContain("'=SUM(1,2)"); expect(csv).toContain("中文,旧委托"); await capture("negative-csv-download", ["waitForEvent('download')", "click export", "saveAs and inspect UTF8"], { path: csvPath, negativeNumeric: true, formulaEscaped: true });
  await page.setViewportSize({ width:1440, height:1000 });
  await page.getByLabel("复盘证券代码").fill("510300"); await page.getByRole("button", { name:"应用复盘筛选" }).click(); const primary = workbench.locator(`[data-review-id="${fixture.planId}"]`); await primary.getByRole("button",{name:"查看计划与实际结果"}).click();
  await primary.getByLabel("本次结论", { exact:true }).fill("跨轮尚未提交草稿，必须保留");
  await page.route(`**/api/reviews/plan/${fixture.planId}/conclusions`,route=>route.fulfill({status:409,json:{detail:{code:"review_conflict",message:"轮次已变化，请刷新事实"}}}));
  await primary.getByRole("button",{name:"保存本次复盘结论",exact:true}).click(); await expect(primary.getByLabel("本次结论",{exact:true})).toHaveValue("跨轮尚未提交草稿，必须保留"); await capture("new-request-round-conflict",["fill unsent draft","return controlled409 round conflict","click save"],{draftRetained:true}); await page.unroute(`**/api/reviews/plan/${fixture.planId}/conclusions`);
  observations.push({engine,name:"extra-complete",passed:true});
 } catch(error) { if(page) {await page.screenshot({path:resolve(output,`${engine}-extra-failure.png`)});await writeFile(resolve(output,`${engine}-extra-failure.txt`),await page.locator("body").innerText());} observations.push({engine,name:"extra-failure",passed:false,error:String(error)});throw error;
 } finally {if(browser) await browser.close();await writeFile(resolve(output,"observations.json"),JSON.stringify(observations,null,2));}
}
console.log(JSON.stringify({passed:true,observations:observations.length,fixture}));
