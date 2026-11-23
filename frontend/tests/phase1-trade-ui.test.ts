import { beforeAll, expect, test } from "bun:test";
import { existsSync } from "node:fs";
import { expect as check, chromium, type Page } from "@playwright/test";

const at = "2026-11-23T15:00:00Z";
const draft = {
	id: "p",
	round_id: "r",
	symbol: "AAPL",
	currency: "USD",
	direction: "buy",
	status: "draft",
	reason: "受控草稿",
	target_price: "99",
	stop_price: "101",
	review_date: null,
	invalidation: "",
	version: 1,
	created_at: at,
	updated_at: at,
	monitoring: {
		status: "pending",
		message: "待观察",
		checked_at: null,
		market_time: null,
		price: null,
		source: null,
	},
};
const assets = {
	round_id: "r",
	initial_capital: "1000",
	total_assets_cny: "1000",
	pnl_cny: "0",
	cash: [],
	positions: [],
	worker: { status: "running", last_run: at },
	valuation_status: "完整",
	warnings: [],
};
const reminders = [
	"提交后，报价仍满足条件时会站内提醒。",
	"只认 20 分钟内的交易时段报价：收盘后约 20 分钟内仍按收盘前报价判断，之后要等下次开市。",
];
const conflict = "该证券刚有进行中计划。可带入该计划，或取消同步后重新预览。";
const todayHint = "计划复盘日期是今天，提交后通常 1–2 分钟内会出现在站内提醒中";
const existingHint =
	"该证券已有进行中计划，可在「带入已有计划」中选择，沿用其提醒";
const retired = ["冻结人民币", "下一轮即会提醒", "将立即产生", "复查日期"];
const preview = {
	price_bound: "100",
	estimated_fee: "0.05",
	reserved_cny: "7",
	reserved_foreign: "0",
	fx_rate: "7",
	fx_source: "受控",
	fx_time: at,
	expires_at: at,
	warnings: [] as string[],
};
// 与后端 Engine.preview_with_context 相同的同步提示顺序；页面只负责原样展示。
function previewFor(body: Record<string, unknown>) {
	if (!body.save_as_plan) return preview;
	return {
		...preview,
		warnings: [
			`提交后将同时创建进行中计划${body.plan_review_date ? `，计划复盘日期 ${body.plan_review_date}` : ""}`,
			"当前报价已达到目标价",
			"当前报价已达到止损参考价",
			...reminders,
		],
	};
}
let js = "",
	css = "";
beforeAll(async () => {
	const build = await Bun.build({
		entrypoints: [
			new URL("./phase1-trade-fixture.tsx", import.meta.url).pathname,
		],
		target: "browser",
		format: "iife",
		define: { "process.env.NODE_ENV": '"production"' },
	});
	if (!build.success) throw new Error("交易窗夹具构建失败");
	for (const asset of build.outputs) {
		if (asset.path.endsWith(".js")) js = await asset.text();
		if (asset.path.endsWith(".css")) css = await asset.text();
	}
});
async function noRetiredText(page: Page) {
	const shown = await page.locator("body").innerText();
	for (const phrase of retired) expect(shown).not.toContain(phrase);
}
async function conflictShownOnce(page: Page) {
	await check(page.getByText(conflict, { exact: true })).toHaveCount(1);
	await check(page.getByRole("alert")).toHaveCount(0);
	await check(page.getByRole("button", { name: "取消同步计划" })).toBeVisible();
	await noRetiredText(page);
}
async function visibleWithoutScroll(page: Page, name: string) {
	const button = page.getByRole("button", { name });
	await check(button).toBeVisible();
	const covered = await button.evaluate((element) => {
		const box = element.getBoundingClientRect();
		const top = document.elementFromPoint(
			box.left + box.width / 2,
			box.top + box.height / 2,
		);
		return {
			inside: box.top >= 0 && box.bottom <= innerHeight,
			topmost: top === element || element.contains(top),
		};
	});
	expect(covered).toEqual({ inside: true, topmost: true });
}
test.each([
	"成功",
	"冲突",
	"查询失败",
	"恢复",
	"已有计划",
	"卖单",
	"预览冲突",
	"重试冲突",
	"桌面",
])(
	"同步计划：%s",
	async (mode) => {
		const executablePath =
			process.env.CHROME_PATH ??
			[
				"/usr/bin/google-chrome",
				"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
			].find(existsSync);
		const browser = await chromium.launch(
			executablePath ? { executablePath } : {},
		);
		const width = mode === "桌面" ? 1024 : 375;
		const page = await browser.newPage({
			viewport: { width, height: mode === "桌面" ? 768 : 812 },
		});
		const ready = Promise.withResolvers<void>();
		const pending = {
			request_id: "pending-order",
			expected_round_id: "r",
			expected_expires_at: at,
			symbol: "AAPL",
			side: "buy",
			order_type: "limit",
			quantity: 1,
			limit_price: "100",
			reason: "受控理由",
			target_price: "99",
			stop_price: "101",
			save_as_plan: true,
			plan_review_date: "2026-11-24",
		};
		let active = mode === "已有计划",
			reads = 0;
		const previews: string[] = [],
			orders: Record<string, unknown>[] = [],
			retries: (string | null)[] = [];
		const { monitoring: _, ...snapshot } = { ...draft, status: "active" };
		try {
			await page.route("**/*", async (route) => {
				const u = new URL(route.request().url());
				if (u.pathname === "/")
					return route.fulfill({
						contentType: "text/html",
						body: '<meta name="viewport" content="width=device-width"><div id="root"></div>',
					});
				if (u.pathname === "/api/plans") {
					const status = u.searchParams.get("status");
					if (status === "active") {
						expect(u.searchParams.get("limit")).toBe("1");
						reads++;
						await ready.promise;
						if (mode === "查询失败")
							return route.fulfill({
								status: 400,
								json: { detail: "受控失败" },
							});
					}
					const activePlan = { ...draft, status: "active" };
					const items =
						status === "active"
							? active
								? [activePlan]
								: []
							: active && !status
								? [activePlan]
								: [draft];
					return route.fulfill({
						json: {
							items,
							total: items.length,
							offset: 0,
							limit: status ? 1 : 25,
						},
					});
				}
				if (u.pathname === "/api/portfolio")
					return route.fulfill({ json: assets });
				if (u.pathname.endsWith("/quote"))
					return route.fulfill({
						json: {
							symbol: "AAPL",
							price: "100",
							currency: "USD",
							market_time: at,
							retrieved_at: at,
							source: "受控",
							status: "reference",
							message: "",
						},
					});
				if (u.pathname === "/api/orders/preview") {
					const body = route.request().postDataJSON();
					previews.push(body.request_id);
					if (mode === "预览冲突" && body.save_as_plan) {
						active = true;
						return route.fulfill({
							status: 409,
							json: {
								detail: { code: "plan_sync_conflict", message: conflict },
							},
						});
					}
					return route.fulfill({ json: previewFor(body) });
				}
				if (u.pathname === "/api/orders" && route.request().method() === "GET")
					return route.fulfill({ json: [] });
				const receipt = u.pathname === "/api/orders/receipts/pending-order";
				if (u.pathname === "/api/orders" || receipt) {
					const body = receipt ? pending : route.request().postDataJSON();
					if (!receipt) {
						orders.push(body);
						retries.push(
							await route.request().headerValue("X-StockMock-Retry"),
						);
					}
					if (mode === "重试冲突") {
						if (retries.length === 1) return route.abort("failed");
						active = true;
						return route.fulfill({
							status: 409,
							json: {
								detail: {
									code: "order_receipt_rejected",
									message: `此编号的委托已拒绝，${conflict}`,
								},
							},
						});
					}
					active = true;
					if (mode === "冲突")
						return route.fulfill({
							status: 409,
							json: {
								detail: { code: "plan_sync_conflict", message: conflict },
							},
						});
					return route.fulfill({
						json: {
							...body,
							...preview,
							id: "order-0001",
							plan_id: "p",
							plan_version: 1,
							plan_snapshot: {
								...snapshot,
								review_date: body.plan_review_date ?? null,
							},
							status: "accepted",
							status_reason: "待成交",
							created_at: at,
							active_at: at,
							cancel_at: null,
							filled_at: null,
							confirmed_at: null,
							fill_price: null,
							fee: null,
							currency: "USD",
						},
					});
				}
				return route.abort();
			});
			await page.goto(
				`http://127.0.0.1:18919/${mode === "卖单" ? "?side=sell" : ""}`,
			);
			await page.addScriptTag({ content: js });
			await page.addStyleTag({ content: css });
			const box = page.getByRole("checkbox", { name: /同时保存/ }),
				reason = page.getByLabel("这次交易的理由"),
				run = page.getByRole("button", { name: "预览费用与资金占用" });
			await reason.fill("受控理由");
			if (mode === "卖单") {
				ready.resolve();
				await page.getByText("记录目标价与止损参考价").click();
				await page.getByLabel("目标价").fill("120");
				await page.getByLabel("限价（USD）").fill("100");
				await check(run).toBeEnabled();
				await check(box).toHaveCount(0);
				await check(page.getByText(existingHint)).toHaveCount(0);
				expect(reads).toBe(0);
				await run.click();
				const view = page.getByRole("region", { name: "委托预览" });
				await check(view).toContainText(
					"占用持仓1 股 / 份（撤单确认前不可再卖）",
				);
				await check(view).not.toContainText("占用人民币");
				await visibleWithoutScroll(page, "确认提交委托");
				return;
			}
			if (mode === "已有计划") {
				ready.resolve();
				await check(page.getByText(existingHint)).toBeVisible();
				await check(box).toHaveCount(0);
				await page.getByLabel("带入已有计划").selectOption("p");
				await check(page.getByText(existingHint)).toHaveCount(0);
				await noRetiredText(page);
				return;
			}
			await check(page.getByText(/已有草稿计划/)).toBeVisible();
			await check(box).toHaveCount(0);
			ready.resolve();
			if (mode === "查询失败") {
				await check.poll(() => reads).toBeGreaterThan(0);
				await check(run).toBeEnabled();
				await check(box).toHaveCount(0);
				await check(page.getByText(existingHint)).toHaveCount(0);
				return;
			}
			if (mode === "恢复") {
				await check(box).toBeVisible();
				await page.getByRole("button", { name: "关闭", exact: true }).click();
				await page.evaluate(
					(value) =>
						sessionStorage.setItem(
							"stockmock.pending-order",
							JSON.stringify(value),
						),
					pending,
				);
				await page.getByRole("button", { name: "核对委托" }).click();
				await check(page.getByRole("dialog")).toContainText(
					"已同时创建 AAPL 的进行中计划。",
				);
				await check(page.getByRole("dialog")).toContainText(
					"计划复盘日期 2026-11-24",
				);
				await noRetiredText(page);
				await page.getByRole("button", { name: "知道了" }).click();
				await page.getByRole("button", { name: "再次买入" }).click();
				await reason.fill("受控恢复后再次交易");
				await check(
					page
						.getByLabel("带入已有计划")
						.getByRole("option", { name: /进行中/ }),
				).toHaveCount(1);
				await check(box).toHaveCount(0);
				await check(page.getByText(existingHint)).toBeVisible();
				expect(reads).toBeGreaterThan(1);
				expect(orders).toHaveLength(0);
				return;
			}
			await box.check();
			await page.getByLabel("带入已有计划").selectOption("p");
			await check(box).toHaveCount(0);
			await page.getByLabel("带入已有计划").selectOption("");
			await check(box).not.toBeChecked();
			await box.check();
			await page.getByLabel("限价（USD）").fill("100");
			if (mode === "预览冲突") {
				await run.click();
				await conflictShownOnce(page);
				await check(run).toBeDisabled();
				await page.getByRole("button", { name: "取消同步计划" }).click();
				await check(run).toBeEnabled();
				await check(page.getByText(conflict)).toHaveCount(0);
				await check(page.getByText(existingHint)).toBeVisible();
				return;
			}
			const date = page.getByLabel("计划复盘日期（北京时间，选填）");
			const today = await date.getAttribute("min");
			await date.fill(today!);
			await check(page.getByText(todayHint)).toBeVisible();
			await run.click();
			const view = page.getByRole("region", { name: "委托预览" });
			await check(view).toContainText(
				`提交后将同时创建进行中计划，计划复盘日期 ${today}`,
			);
			expect(
				(await view.innerText()).split(`计划复盘日期 ${today}`).length - 1,
			).toBe(1);
			await check(view).toContainText("当前报价已达到目标价");
			await check(view).toContainText("当前报价已达到止损参考价");
			for (const line of reminders)
				await check(view.getByText(line, { exact: true })).toHaveCount(1);
			await noRetiredText(page);
			expect(
				await page.evaluate(() => document.documentElement.scrollWidth),
			).toBeLessThanOrEqual(width);
			const actions = page.locator("form > div:has(> button[type=submit])");
			if (mode === "桌面") {
				expect(
					await actions.evaluate((e) => getComputedStyle(e).position),
				).toBe("static");
				await check(actions).not.toHaveClass(/trade-actions/);
				return;
			}
			await check(actions).toHaveClass("trade-actions");
			await visibleWithoutScroll(page, "确认提交委托");
			await date.fill("");
			await check(view).toHaveCount(0);
			await check(actions).not.toHaveClass(/trade-actions/);
			await run.click();
			await check(view).not.toContainText("计划复盘日期");
			await page.getByRole("button", { name: "确认提交委托" }).click();
			await check.poll(() => orders.length).toBe(1);
			expect(new Set(previews).size).toBe(2);
			expect(orders[0]).toMatchObject({
				save_as_plan: true,
				request_id: previews.at(-1),
			});
			expect(orders[0]).not.toHaveProperty("plan_review_date");
			if (mode === "重试冲突") {
				const retry = page.getByRole("button", { name: "重试确认委托结果" });
				await check(retry).toBeVisible();
				await check(actions).toHaveClass("trade-actions");
				await check(actions.getByText(/委托结果尚未确认/)).toBeVisible();
				await visibleWithoutScroll(page, "重试确认委托结果");
				await retry.click();
				await check.poll(() => retries).toEqual([null, "1"]);
				await conflictShownOnce(page);
				await check(run).toBeDisabled();
				await page.getByRole("button", { name: "取消同步计划" }).click();
				await check(run).toBeEnabled();
				return;
			}
			if (mode === "成功") {
				await check(page.getByRole("status").first()).toContainText(
					"编号 order-00。已同时创建 AAPL 的进行中计划。最新状态见委托记录。",
				);
				await page.getByRole("button", { name: "再次买入" }).click();
				await reason.fill("再次交易");
			} else await conflictShownOnce(page);
			await check(
				page.getByLabel("带入已有计划").getByRole("option", { name: /进行中/ }),
			).toHaveCount(1);
			await check(box).toHaveCount(0);
			expect(reads).toBeGreaterThan(1);
			if (mode === "成功")
				await check(page.getByText(existingHint)).toBeVisible();
			if (mode === "冲突") {
				await check(run).toBeDisabled();
				await page.getByRole("button", { name: "取消同步计划" }).click();
				await check(run).toBeEnabled();
				await check(page.getByText(existingHint)).toBeVisible();
			}
		} finally {
			ready.resolve();
			await browser.close();
		}
	},
	25000,
);
