import { beforeAll, expect, test } from "bun:test";
import { existsSync } from "node:fs";
import { chromium, expect as check } from "@playwright/test";
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
const reminder =
	"提交后，后台取到 20 分钟内的有效报价且仍满足条件时会提醒；刚收盘时仍可能按收盘前的报价提醒，之后要等下次开市。";
const conflict = "该证券刚有进行中计划，请在计划中选择后重新预览";
const preview = {
	price_bound: "100",
	estimated_fee: "0.05",
	reserved_cny: "7",
	reserved_foreign: "0",
	fx_rate: "7",
	fx_source: "受控",
	fx_time: at,
	expires_at: at,
	warnings: [
		"提交后将同时创建进行中计划",
		"当前报价已达到目标价",
		"当前报价已达到止损参考价",
		reminder,
	],
};
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
test.each(["成功", "冲突", "查询失败", "恢复"])(
	"375px 同步计划：%s",
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
		const page = await browser.newPage({
			viewport: { width: 375, height: 812 },
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
		let active = false,
			reads = 0;
		const previews: string[] = [],
			orders: Record<string, unknown>[] = [];
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
					expect(body.save_as_plan).toBe(true);
					previews.push(body.request_id);
					return route.fulfill({ json: preview });
				}
				const receipt = u.pathname === "/api/orders/receipts/pending-order";
				if (u.pathname === "/api/orders" || receipt) {
					const body = receipt ? pending : route.request().postDataJSON();
					if (!receipt) orders.push(body);
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
							id: "order",
							plan_id: "p",
							plan_version: 1,
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
			await page.goto("http://127.0.0.1:18919/");
			await page.addScriptTag({ content: js });
			await page.addStyleTag({ content: css });
			const box = page.getByRole("checkbox", { name: /同时保存/ }),
				reason = page.getByLabel("这次交易的理由");
			await reason.fill("受控理由");
			await check(page.getByText(/已有草稿计划/)).toBeVisible();
			await check(box).toHaveCount(0);
			ready.resolve();
			if (mode === "查询失败") {
				await check.poll(() => reads).toBeGreaterThan(0);
				await check(
					page.getByRole("button", { name: "预览费用与资金占用" }),
				).toBeEnabled();
				await check(box).toHaveCount(0);
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
					"已同时创建进行中计划",
				);
				await check(page.getByRole("dialog")).toContainText(
					"复查日期 2026-11-24",
				);
				await page.getByRole("button", { name: "知道了" }).click();
				await page.getByRole("button", { name: "再次买入" }).click();
				await reason.fill("受控恢复后再次交易");
				await check(
					page
						.getByLabel("带入已有计划")
						.getByRole("option", { name: /进行中/ }),
				).toHaveCount(1);
				await check(box).toHaveCount(0);
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
			const date = page.getByLabel(/复查日期（北京时间/);
			const today = await date.getAttribute("min");
			await date.fill(today!);
			await check(page.getByText("下一轮即会提醒")).toBeVisible();
			const run = page.getByRole("button", { name: "预览费用与资金占用" });
			await run.click();
			const view = page.getByRole("region", { name: "委托预览" });
			await check(view).toContainText("当前报价已达到目标价");
			await check(view).toContainText("当前报价已达到止损参考价");
			await check(view).toContainText(reminder);
			await check(view).not.toContainText("将立即产生");
			expect(
				await page.evaluate(() => document.documentElement.scrollWidth),
			).toBeLessThanOrEqual(375);
			await date.fill("");
			await check(view).toHaveCount(0);
			await run.click();
			await page.getByRole("button", { name: "确认提交委托" }).click();
			await check.poll(() => orders.length).toBe(1);
			expect(new Set(previews).size).toBe(2);
			expect(orders[0]).toMatchObject({
				save_as_plan: true,
				request_id: previews.at(-1),
			});
			expect(orders[0]).not.toHaveProperty("plan_review_date");
			if (mode === "成功") {
				await page.getByRole("button", { name: "再次买入" }).click();
				await reason.fill("再次交易");
			} else await check(page.getByRole("alert")).toContainText(conflict);
			await check(
				page.getByLabel("带入已有计划").getByRole("option", { name: /进行中/ }),
			).toHaveCount(1);
			await check(box).toHaveCount(0);
			expect(reads).toBeGreaterThan(1);
			if (mode === "冲突") {
				await check(run).toBeDisabled();
				await page.getByRole("button", { name: "取消同步计划" }).click();
				await check(run).toBeEnabled();
			}
		} finally {
			ready.resolve();
			await browser.close();
		}
	},
	25000,
);
