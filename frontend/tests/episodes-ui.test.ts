import { afterEach, beforeEach, expect, spyOn, test } from "bun:test";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { Episodes, episodesQuery } from "../src/Episodes";
import { episodesSchema } from "../src/schema";

// 受控数据只覆盖 04 接口契约，不连接任何账户服务。
const payload = episodesSchema.parse({
	symbol: "AAPL",
	name: "受控苹果",
	currency: "USD",
	round_id: "r-1",
	as_of: "2026-11-28T04:00:00Z",
	market_today: "2026-11-27",
	reconciled: false,
	reconcile_note: "当前持仓 100 股/份，重放终值 90 股/份。",
	episodes: [
		{
			index: 2,
			status: "open",
			opened_at: "2026-11-26T14:30:00Z",
			closed_at: null,
			opened_market_date: "2026-11-26",
			closed_market_date: null,
			holding_days: 1,
			buy_count: 2,
			sell_count: 0,
			peak_quantity: 200,
			boundary_note: null,
			events: [
				{
					kind: "first_buy",
					at: "2026-11-26T14:30:00Z",
					market_date: "2026-11-26",
					currency: "USD",
					quantity: 100,
					price: "100.500",
					fee: "5.03",
					position_after: 100,
					order_id: "order-first",
					ratio: null,
					cash_per_share: null,
					cash_amount: null,
					payment_status: null,
					note: null,
				},
				{
					kind: "dividend",
					at: "2026-11-27T21:00:00Z",
					market_date: "2026-11-27",
					currency: "USD",
					quantity: 100,
					price: null,
					fee: null,
					position_after: 100,
					order_id: null,
					ratio: null,
					cash_per_share: "0.500",
					cash_amount: "50.00",
					payment_status: "receivable",
					note: null,
				},
			],
		},
		{
			index: 1,
			status: "closed",
			opened_at: "2026-11-01T14:00:00Z",
			closed_at: "2026-11-04T21:00:00Z",
			opened_market_date: "2026-11-01",
			closed_market_date: "2026-11-04",
			holding_days: 3,
			buy_count: 1,
			sell_count: 1,
			peak_quantity: 150,
			boundary_note: "拆合股前数量核对不上，本段首买或清仓边界可能不准。",
			events: [
				{
					kind: "close",
					at: "2026-11-04T21:00:00Z",
					market_date: "2026-11-04",
					currency: "USD",
					quantity: 150,
					price: "110.000",
					fee: "8.25",
					position_after: 0,
					order_id: "order-close",
					ratio: null,
					cash_per_share: null,
					cash_amount: null,
					payment_status: null,
					note: null,
				},
			],
		},
	],
	outside_events: [
		{
			kind: "dividend",
			at: "2026-10-20T16:00:00Z",
			market_date: "2026-10-20",
			currency: "USD",
			quantity: 0,
			price: null,
			fee: null,
			position_after: 0,
			order_id: null,
			ratio: null,
			cash_per_share: "1.000",
			cash_amount: "0.00",
			payment_status: "paid",
			note: "当时没有持仓，未计入任何段。",
		},
	],
});

const OriginalRequest = globalThis.Request;
const originalFetch = globalThis.fetch;
let episodesResponse: unknown;
let client: QueryClient;
let clock: ReturnType<typeof spyOn>;

beforeEach(() => {
	clock = spyOn(Date, "now").mockImplementation(() =>
		Date.parse("2026-11-28T05:00:00Z"),
	);
	client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
	episodesResponse = payload;
	globalThis.Request = class extends OriginalRequest {
		constructor(input: RequestInfo | URL, init?: RequestInit) {
			super(
				typeof input === "string"
					? new URL(input, "http://episodes-test.invalid")
					: input,
				init,
			);
		}
	};
	globalThis.fetch = Object.assign(
		async (input: RequestInfo | URL) => {
			const request = input as Request;
			expect(request.url).toBe(
				"http://episodes-test.invalid/api/positions/AAPL/episodes",
			);
			expect(request.method).toBe("GET");
			return Response.json(episodesResponse);
		},
		{ preconnect: originalFetch.preconnect },
	);
});
afterEach(() => {
	client.clear();
	clock.mockRestore();
	globalThis.Request = OriginalRequest;
	globalThis.fetch = originalFetch;
});

const markup = () =>
	renderToStaticMarkup(
		createElement(
			QueryClientProvider,
			{ client },
			createElement(Episodes, {
				symbol: "AAPL",
				onLocateOrder: () => {},
			}),
		),
	);
const load = async () => {
	await client.fetchQuery(episodesQuery("AAPL"));
};

test("默认展开最近一段，其余段折叠为一行摘要", async () => {
	await load();
	const html = markup();
	// 最近一段（进行中）展开：摘要与时间线同时可见。
	expect(html).toContain(
		"第 2 段 · 进行中 · 11/26 首买 · 已持有 1 天 · 买 2 次 卖 0 次",
	);
	expect(html).toContain("买入 100 股/份 × US$100.500");
	expect(html).toContain("费用 US$5.03");
	// 其余段折叠：只显示一行摘要（含边界说明），不渲染该段时间线。
	expect(html).toContain(
		"第 1 段 · 已清仓 · 11/01 首买 · 11/04 清仓 · 持有 3 天 · 买 1 次 卖 1 次 · 拆合股前数量核对不上，本段首买或清仓边界可能不准。",
	);
	expect(html).not.toContain("order-close");
	expect(html).not.toContain("卖出 150 股/份");
});

test("时间线行含股息、市场日与事件后持仓，成交行可跳转委托记录", async () => {
	await load();
	const html = markup();
	expect(html).toContain("每股股息 US$0.500");
	expect(html).toContain("股息 US$50.00（应收）");
	// 北京时间 11/28 05:00 与市场日 11/27 不是同一天，补充市场日。
	expect(html).toContain("11/28 05:00（北京时间） · 市场日 11/27");
	expect(html).toContain("事件后持仓 100 股/份");
	// 展开的最近一段只有首买成交行带跳转按钮。
	expect(html).toContain('data-order-id="order-first"');
	expect(html.match(/在委托记录中查看/g)).toHaveLength(1);
});

test("重放与当前持仓不一致时顶部文字说明，段外事件单列", async () => {
	await load();
	const html = markup();
	expect(html).toContain("当前持仓 100 股/份，重放终值 90 股/份。");
	expect(html).toContain("段外事件");
	expect(html).toContain("当时没有持仓，未计入任何段。");
	expect(html).toContain("首买时间");
	expect(html).toContain("2026-11-26 22:30:00（北京时间）");
	expect(html).toContain("段内最大持仓 200 股/份");
});

test("本轮无成交与调整时不占位", async () => {
	episodesResponse = episodesSchema.parse({
		...payload,
		episodes: [],
		outside_events: [],
	});
	await load();
	expect(markup()).toBe("");
});
