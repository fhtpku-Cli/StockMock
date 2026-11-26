import { beforeAll, expect, test } from "bun:test";
import { existsSync } from "node:fs";
import { expect as check, chromium, type Page } from "@playwright/test";
import { LOCATE_MISSING } from "../src/Orders";
import { RECENT_ENDINGS_MORE } from "../src/RecentEndings";
import { orderSchema, recentEndingsSchema } from "../src/schema";

let js = "",
	css = "";
beforeAll(async () => {
	const build = await Bun.build({
		entrypoints: [
			new URL("./recent-endings-fixture.tsx", import.meta.url).pathname,
		],
		target: "browser",
		format: "iife",
		define: { "process.env.NODE_ENV": '"production"' },
	});
	if (!build.success) throw new Error("最近结束委托夹具构建失败");
	for (const asset of build.outputs) {
		if (asset.path.endsWith(".js")) js = await asset.text();
		if (asset.path.endsWith(".css")) css = await asset.text();
	}
});

// 受控数据只覆盖 09 接口契约，不连接任何账户服务。
const endingsData = recentEndingsSchema.parse({
	window_start: "2026-11-25T00:00:00+08:00",
	window_end: "2026-11-27T10:30:00+08:00",
	items: [
		{
			order_id: "order-filled-1",
			symbol: "AAPL",
			name: "受控苹果",
			side: "buy",
			quantity: 10,
			status: "filled",
			status_reason: "后台延迟处理，按市场时间 22:00 的完整区间成交",
			currency: "USD",
			fill_price: "100.50",
			filled_at: "2026-11-27T05:00:00+08:00",
			ended_at: "2026-11-27T05:00:01+08:00",
			cash_changes: [
				{ currency: "CNY", amount: "-7035.00" },
				{ currency: "USD", amount: "-1000.50" },
				{ currency: "HKD", amount: "0.00" },
			],
			released_cny: null,
			released_foreign: null,
			released_shares: null,
		},
		{
			order_id: "order-cancel-sell",
			symbol: "0700.HK",
			name: null,
			side: "sell",
			quantity: 100,
			status: "cancelled",
			status_reason: "手动撤销",
			currency: "HKD",
			fill_price: null,
			filled_at: null,
			ended_at: "2026-11-26T09:31:00+08:00",
			cash_changes: [],
			released_cny: null,
			released_foreign: null,
			released_shares: 100,
		},
		{
			order_id: "order-expired-buy",
			symbol: "600519.SH",
			name: "受控茅台",
			side: "buy",
			quantity: 2,
			status: "expired",
			status_reason: "有效期结束未成交",
			currency: "CNY",
			fill_price: null,
			filled_at: null,
			ended_at: "2026-11-26T03:00:00+08:00",
			cash_changes: [],
			released_cny: "15000.00",
			released_foreign: null,
			released_shares: null,
		},
		{
			order_id: "order-terminated-buy",
			symbol: "MSFT",
			name: "受控微软",
			side: "buy",
			quantity: 1,
			status: "terminated",
			status_reason: "练习结束终止",
			currency: "USD",
			fill_price: null,
			filled_at: null,
			ended_at: "2026-11-25T06:00:00+08:00",
			cash_changes: [],
			released_cny: "700.00",
			released_foreign: "10.00",
			released_shares: null,
		},
		{
			order_id: "order-vanished",
			symbol: "BBBB",
			name: null,
			side: "buy",
			quantity: 1,
			status: "terminated",
			status_reason: "受控缺失委托",
			currency: "CNY",
			fill_price: null,
			filled_at: null,
			ended_at: "2026-11-25T07:00:00+08:00",
			cash_changes: [],
			released_cny: null,
			released_foreign: null,
			released_shares: null,
		},
	],
	more: false,
});

const orderBase = {
	request_id: "req-1",
	order_type: "limit" as const,
	limit_price: "100",
	price_bound: "100",
	created_at: "2026-11-26T14:00:00Z",
	active_at: "2026-11-26T14:00:00Z",
	expires_at: "2026-11-28T20:00:00Z",
	cancel_at: null,
	filled_at: null,
	confirmed_at: null,
	fill_price: null,
	fee: null,
	reserved_cny: "700",
	reserved_foreign: "0",
	fx_rate: "7",
	fx_source: "受控数据",
	fx_time: "2026-11-26T14:00:00Z",
	reason: "受控定位测试",
	target_price: null,
	stop_price: null,
};
const acceptedOrder = orderSchema.parse({
	...orderBase,
	id: "order-live-1",
	symbol: "AAPL",
	side: "buy",
	quantity: 1,
	status: "accepted",
	status_reason: "待成交",
	currency: "USD",
});
const cancelledOrder = orderSchema.parse({
	...orderBase,
	id: "order-cancel-sell",
	symbol: "0700.HK",
	side: "sell",
	quantity: 100,
	status: "cancelled",
	status_reason: "手动撤销",
	currency: "HKD",
	reserved_cny: "0",
});
const fillers = Array.from({ length: 14 }, (_, i) =>
	orderSchema.parse({
		...orderBase,
		id: `order-filler-${String(i + 1).padStart(2, "0")}`,
		request_id: `req-filler-${i}`,
		symbol: "AAPL",
		side: "buy",
		quantity: 1,
		status: "accepted",
		status_reason: "待成交",
		currency: "USD",
	}),
);

type State = {
	endings: typeof endingsData;
	endingsFail: boolean;
	endingReads: number;
	cancelReads: number;
	placedOrders: number;
	cancelled: boolean;
};

const newState = (endings: State["endings"]): State => ({
	endings,
	endingsFail: false,
	endingReads: 0,
	cancelReads: 0,
	placedOrders: 0,
	cancelled: false,
});

/** 打开夹具页并接管全部请求；init 在夹具脚本注入前运行，可预置本地存储。 */
async function openPage(
	state: State,
	options: {
		readonly query?: string;
		readonly width?: number;
		readonly init?: (page: Page) => Promise<void>;
	} = {},
) {
	const executablePath =
		process.env.CHROME_PATH ??
		[
			"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
			"/usr/bin/google-chrome",
		].find(existsSync);
	const browser = await chromium.launch(
		executablePath ? { executablePath } : {},
	);
	const page = await browser.newPage({
		viewport: { width: options.width ?? 1280, height: 720 },
	});
	await page.route("**/*", async (route) => {
		const u = new URL(route.request().url());
		if (u.pathname === "/")
			return route.fulfill({
				contentType: "text/html",
				body: '<meta name="viewport" content="width=device-width"><div id="root"></div>',
			});
		if (u.pathname === "/api/orders/recent-endings") {
			state.endingReads++;
			if (state.endingsFail)
				return route.fulfill({ status: 400, json: { detail: "受控失败" } });
			return route.fulfill({ json: state.endings });
		}
		if (u.pathname === "/api/orders" && route.request().method() === "GET")
			return route.fulfill({
				json: [
					...fillers,
					state.cancelled
						? { ...acceptedOrder, status: "cancel_pending" as const }
						: acceptedOrder,
					cancelledOrder,
				],
			});
		if (u.pathname === "/api/orders/order-live-1/cancel") {
			state.cancelReads++;
			state.cancelled = true;
			return route.fulfill({
				json: { ...acceptedOrder, status: "cancel_pending" },
			});
		}
		if (u.pathname === "/api/orders" && route.request().method() === "POST") {
			state.placedOrders++;
			return route.fulfill({
				json: { ...acceptedOrder, id: "order-placed-1", status: "accepted" },
			});
		}
		if (u.pathname === "/api/orders/preview")
			return route.fulfill({
				json: {
					price_bound: "100",
					estimated_fee: "0.05",
					reserved_cny: "700",
					reserved_foreign: "0",
					fx_rate: "7",
					fx_source: "受控",
					fx_time: "2026-11-27T02:00:00Z",
					expires_at: "2026-11-27T02:20:00Z",
					warnings: [],
				},
			});
		if (u.pathname === "/api/portfolio")
			return route.fulfill({
				json: {
					round_id: "r1",
					initial_capital: "100000",
					total_assets_cny: "100000",
					pnl_cny: "0",
					valuation_status: "完整",
					cash: [
						{
							currency: "USD",
							balance: "1000",
							reserved: "0",
							available: "1000",
						},
						{
							currency: "CNY",
							balance: "90000",
							reserved: "0",
							available: "90000",
						},
					],
					positions: [],
					worker: { status: "running", last_run: "2026-11-27T02:00:00Z" },
					warnings: [],
				},
			});
		if (u.pathname.endsWith("/quote"))
			return route.fulfill({
				json: {
					symbol: "AAPL",
					price: "100",
					currency: "USD",
					market_time: "2026-11-27T02:00:00Z",
					retrieved_at: "2026-11-27T02:00:00Z",
					source: "受控",
					status: "reference",
					message: "",
				},
			});
		if (u.pathname === "/api/plans") {
			const status = u.searchParams.get("status");
			return route.fulfill({
				json: { items: [], total: 0, offset: 0, limit: status ? 1 : 25 },
			});
		}
		return route.abort();
	});
	await page.goto(`http://127.0.0.1:18921/${options.query ?? ""}`);
	if (options.init) await options.init(page);
	await page.addScriptTag({ content: js });
	await page.addStyleTag({ content: css });
	return { browser, page };
}

test("四种结束状态的文案、多币种分行与卖单持仓释放", async () => {
	const state = newState(endingsData);
	const { browser, page } = await openPage(state);
	try {
		const panel = page.locator(".recent-endings");
		await check(panel).toContainText("最近 3 天结束的委托 5 笔");
		await check(panel).toContainText("统计北京时间 2026-11-25 00:00:00");
		const filled = page.locator('[data-ending-id="order-filled-1"]');
		await check(filled).toContainText("受控苹果（AAPL）");
		await check(filled).toContainText("已成交");
		await check(filled).toContainText("买入 10 股 / 份");
		await check(filled).toContainText("成交价 US$100.50");
		await check(filled).toContainText("结束于 2026-11-27 05:00:01（北京时间）");
		// 成交单状态说明原样显示，后台延迟确认不另设判断。
		await check(filled).toContainText(
			"后台延迟处理，按市场时间 22:00 的完整区间成交",
		);
		const changes = filled.locator(".recent-ending-changes li");
		await check(changes).toHaveCount(2);
		await check(changes.nth(0)).toHaveText("人民币 −¥7,035.00");
		await check(changes.nth(1)).toHaveText("美元 −US$1,000.50");
		const sell = page.locator('[data-ending-id="order-cancel-sell"]');
		await check(sell.locator("strong")).toHaveText("0700.HK");
		await check(sell).toContainText("已撤销");
		await check(sell).toContainText("卖出 100 股 / 份");
		await check(sell).toContainText("释放占用持仓 100 股 / 份");
		const expired = page.locator('[data-ending-id="order-expired-buy"]');
		await check(expired).toContainText("已过期");
		await check(expired).toContainText("释放占用 人民币 ¥15,000.00");
		const terminated = page.locator('[data-ending-id="order-terminated-buy"]');
		await check(terminated).toContainText("已终止");
		await check(terminated).toContainText("释放占用 人民币 ¥700.00");
		await check(terminated).toContainText("释放占用 美元 US$10.00");
		// more 为假时不提示其余委托。
		await check(page.getByText(RECENT_ENDINGS_MORE)).toHaveCount(0);
	} finally {
		await browser.close();
	}
});

test("375px 宽度下不横向溢出且文字不小于 12px", async () => {
	const state = newState(endingsData);
	const { browser, page } = await openPage(state, { width: 375 });
	try {
		await check(page.locator(".recent-endings")).toContainText(
			"最近 3 天结束的委托 5 笔",
		);
		expect(
			await page.evaluate(() => document.documentElement.scrollWidth),
		).toBeLessThanOrEqual(375);
		const sizes = await page.evaluate(() =>
			Array.from(
				document.querySelectorAll(".recent-endings p, .recent-endings small"),
			).map((el) => Number.parseFloat(getComputedStyle(el).fontSize)),
		);
		expect(sizes.length).toBeGreaterThan(0);
		for (const size of sizes) expect(size).toBeGreaterThanOrEqual(12);
	} finally {
		await browser.close();
	}
});

test("more 为真时提示去委托记录按创建日期查看其余委托", async () => {
	const state = newState({ ...endingsData, more: true });
	const { browser, page } = await openPage(state);
	try {
		await check(page.locator(".recent-endings")).toContainText(
			"最近 3 天结束的委托 5 笔",
		);
		await check(page.getByText(RECENT_ENDINGS_MORE)).toBeVisible();
	} finally {
		await browser.close();
	}
});

test("结束时间晚于已看到位置的条目标新，渲染后前移已看到位置", async () => {
	const state = newState(endingsData);
	const { browser, page } = await openPage(state, {
		init: async (p) => {
			await p.evaluate(() =>
				localStorage.setItem(
					"stockmock:recent-endings-seen:r1",
					"2026-11-26T03:00:00+08:00",
				),
			);
		},
	});
	try {
		const panel = page.locator(".recent-endings");
		await check(panel).toContainText(
			"最近 3 天结束的委托 5 笔 · 其中 2 笔新出现",
		);
		await check(
			page.locator('[data-ending-id="order-filled-1"] .badge.warn'),
		).toHaveText("新");
		await check(
			page.locator('[data-ending-id="order-cancel-sell"] .badge.warn'),
		).toHaveText("新");
		await check(page.locator(".recent-endings .badge.warn")).toHaveCount(2);
		// 已看到位置只取接口返回的最大结束时间，不用设备时钟。
		await check
			.poll(() =>
				page.evaluate(() =>
					localStorage.getItem("stockmock:recent-endings-seen:r1"),
				),
			)
			.toBe("2026-11-27T05:00:01+08:00");
	} finally {
		await browser.close();
	}
});

test("localStorage 读写失败时照常显示且不加新标记", async () => {
	const state = newState(endingsData);
	const { browser, page } = await openPage(state, {
		init: async (p) => {
			await p.evaluate(() => {
				Storage.prototype.getItem = () => {
					throw new Error("受控禁止");
				};
				Storage.prototype.setItem = () => {
					throw new Error("受控禁止");
				};
			});
		},
	});
	try {
		const panel = page.locator(".recent-endings");
		await check(panel).toContainText("最近 3 天结束的委托 5 笔");
		await check(panel).not.toContainText("新出现");
		await check(page.locator(".recent-endings .badge.warn")).toHaveCount(0);
		await check(
			page.locator('[data-ending-id="order-filled-1"]'),
		).toBeVisible();
	} finally {
		await browser.close();
	}
});

test("读取失败可重试且不影响资产页其他部分", async () => {
	const state = newState(endingsData);
	state.endingsFail = true;
	const { browser, page } = await openPage(state);
	try {
		await check(page.getByText("最近结束的委托暂不可用")).toBeVisible();
		await check(page.getByText("受控失败")).toBeVisible();
		await check(page.getByText("资产页其余部分")).toBeVisible();
		state.endingsFail = false;
		await page.getByRole("button", { name: "重试" }).click();
		await check(page.locator(".recent-endings")).toContainText(
			"最近 3 天结束的委托 5 笔",
		);
	} finally {
		await browser.close();
	}
});

test("定位并高亮指定委托：不显示回执，也不清除待确认记录", async () => {
	const state = newState(endingsData);
	const { browser, page } = await openPage(state, {
		init: async (p) => {
			await p.evaluate(() =>
				sessionStorage.setItem(
					"stockmock.pending-order",
					JSON.stringify({
						request_id: "pending-request-1",
						expected_round_id: "r1",
						expected_expires_at: "2026-11-27T00:00:00+08:00",
						symbol: "AAPL",
						side: "buy",
						order_type: "limit",
						quantity: 1,
						limit_price: "100",
						reason: "受控待确认委托",
					}),
				),
			);
		},
	});
	try {
		await page
			.locator('[data-ending-id="order-cancel-sell"]')
			.getByRole("button", { name: "在委托记录中查看" })
			.click();
		const row = page.locator('[data-order-id="order-cancel-sell"]');
		await check(row).toHaveClass(/located/);
		await check(row).toHaveAttribute("aria-current", "true");
		// 滚动到该委托后它应位于视口内并接收焦点。
		const inView = await row.evaluate((element) => {
			const box = element.getBoundingClientRect();
			return box.top >= 0 && box.bottom <= innerHeight;
		});
		expect(inView).toBe(true);
		expect(
			await page.evaluate(() =>
				document.activeElement?.getAttribute("data-order-id"),
			),
		).toBe("order-cancel-sell");
		// 不显示回执，也不清除待确认记录。
		await check(page.getByText(/已收到/)).toHaveCount(0);
		expect(
			await page.evaluate(() =>
				sessionStorage.getItem("stockmock.pending-order"),
			),
		).toContain("pending-request-1");
		// 短暂高亮：数秒后标记消失。
		await check
			.poll(() => row.evaluate((element) => element.className), {
				timeout: 7000,
			})
			.not.toContain("located");
	} finally {
		await browser.close();
	}
}, 20000);

test("委托记录中找不到时提示按创建日期查找", async () => {
	const state = newState(endingsData);
	const { browser, page } = await openPage(state);
	try {
		await page
			.locator('[data-ending-id="order-vanished"]')
			.getByRole("button", { name: "在委托记录中查看" })
			.click();
		await check(page.getByText(LOCATE_MISSING)).toBeVisible();
		await check(page.locator(".order-row.located")).toHaveCount(0);
	} finally {
		await browser.close();
	}
});

test("撤单成功后最近结束委托失效重取", async () => {
	const state = newState(endingsData);
	const { browser, page } = await openPage(state);
	try {
		await check(page.locator(".recent-endings")).toContainText(
			"最近 3 天结束的委托 5 笔",
		);
		expect(state.endingReads).toBe(1);
		await page
			.locator('[data-order-id="order-live-1"]')
			.getByRole("button", { name: "申请撤单" })
			.click();
		await page
			.getByRole("dialog", { name: "确认申请撤单" })
			.getByRole("button", { name: "确认申请撤单" })
			.click();
		await check(page.locator('[data-order-id="order-live-1"]')).toContainText(
			"撤单确认中",
		);
		expect(state.cancelReads).toBe(1);
		await check.poll(() => state.endingReads).toBeGreaterThan(1);
	} finally {
		await browser.close();
	}
});

test("下单成功后最近结束委托失效重取", async () => {
	const state = newState(endingsData);
	const { browser, page } = await openPage(state, { query: "?trade=1" });
	try {
		await check(page.locator(".recent-endings")).toContainText(
			"最近 3 天结束的委托 5 笔",
		);
		expect(state.endingReads).toBe(1);
		await page.getByLabel("限价（USD）").fill("100");
		await page.getByRole("button", { name: "预览费用与资金占用" }).click();
		await page.getByRole("button", { name: "确认提交委托" }).click();
		await check.poll(() => state.placedOrders).toBe(1);
		await check.poll(() => state.endingReads).toBeGreaterThan(1);
	} finally {
		await browser.close();
	}
}, 20000);
