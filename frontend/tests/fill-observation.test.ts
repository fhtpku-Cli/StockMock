import { describe, expect, it } from "bun:test";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { OrderRecord } from "../src/OrderRecord";
import { ReviewFacts } from "../src/ReviewFacts";
import { orderSchema } from "../src/schema";

const observation = {
	symbol: "600519.SS",
	currency: "CNY",
	source: "受控成交来源",
	interval_start: "2026-10-22T02:00:00Z",
	market_time: "2026-10-22T02:05:00Z",
	retrieved_at: "2026-10-22T02:47:12.123456Z",
	price: "99.123456789",
	volume: "1000.25",
	tradable: true,
};
const rawOrder = {
	id: "order",
	request_id: "request",
	symbol: "600519.SS",
	side: "buy",
	order_type: "limit",
	quantity: 100,
	limit_price: "100",
	price_bound: "100",
	status: "filled",
	status_reason: "撤单截止前已有满足条件的完整区间，撤单未生效；后台延迟处理",
	created_at: "2026-10-22T02:00:00Z",
	active_at: "2026-10-22T02:00:00Z",
	expires_at: "2026-10-22T06:57:00Z",
	cancel_at: "2026-10-22T02:10:00Z",
	filled_at: "2026-10-22T02:05:00Z",
	confirmed_at: "2026-10-22T02:47:00Z",
	fill_price: "99.123456789",
	fee: "4.96",
	currency: "CNY",
	reserved_cny: "10005",
	reserved_foreign: "0",
	fx_rate: "1",
	fx_source: "",
	fx_time: null,
	source: "委托参考来源",
	reason: "受控测试",
	target_price: null,
	stop_price: null,
};
const renderOrder = (extra: Record<string, unknown>) =>
	renderToStaticMarkup(
		createElement(OrderRecord, {
			order: orderSchema.parse({ ...rawOrder, ...extra }),
			expanded: true,
		}),
	);
const renderFact = (type: string) => {
	const client = new QueryClient();
	client.setQueryData(["review-facts", "round", "order", "order"], {
		pages: [
			{
				items: [
					{
						id: `${type}:order`,
						type,
						occurred_at: rawOrder.confirmed_at,
						market_time: rawOrder.filled_at,
						order: orderSchema.parse({
							...rawOrder,
							fill_observation: observation,
						}),
					},
				],
				total: 1,
				offset: 0,
				limit: 50,
				round_id: "round",
				read_only: false,
			},
		],
		pageParams: [{ offset: 0, roundId: "" }],
	});
	const html = renderToStaticMarkup(
		createElement(
			QueryClientProvider,
			{ client },
			createElement(ReviewFacts, {
				target: { roundId: "round", subjectType: "order", subjectId: "order" },
			}),
		),
	);
	client.clear();
	return html;
};
describe("成交观察依据", () => {
	it("新字段精确保留十进制与取得时间，拒绝数字金额", () => {
		const parsed = orderSchema.parse({
			...rawOrder,
			fill_observation: observation,
		});
		expect(parsed.fill_observation).toEqual(observation);
		expect(
			orderSchema.safeParse({
				...rawOrder,
				fill_observation: { ...observation, price: 99.12 },
			}).success,
		).toBe(false);
	});
	it("成交卡片和详情显示采用来源、精确价格、北京时间与修订说明，并保留完整结果原因", () => {
		const html = renderOrder({ fill_observation: observation });
		for (const text of [
			"按当时取得的行情成交，后续修订不改本笔记录",
			"受控成交来源",
			"99.123456789",
			"行情区间（北京时间）",
			"取得时间（北京时间）",
			"后来修订的历史图表",
			rawOrder.status_reason,
		])
			expect(html).toContain(text);
	});
	it("旧成交的缺字段和null均显示历史缺失，不补造取得时间", () => {
		for (const extra of [{}, { fill_observation: null }]) {
			const html = renderOrder(extra);
			expect(html).toContain("历史记录未保存完整行情依据");
			expect(html).not.toContain("取得时间（北京时间）");
		}
	});
	it("活动订单即使带有快照也不能显示已成交解释", () => {
		for (const status of ["accepted", "cancel_pending"]) {
			const html = renderOrder({
				status,
				fill_price: null,
				filled_at: null,
				confirmed_at: null,
				fee: null,
				status_reason: "等待",
				fill_observation: observation,
			});
			expect(html).not.toContain("按当时取得的行情成交");
			expect(html).not.toContain("受控成交来源");
			expect(html).not.toContain("历史记录未保存完整行情依据");
		}
	});
	it("复盘只在成交事件显示依据，即使早期事实误带快照也不展示", () => {
		expect(renderFact("order_filled")).toContain("受控成交来源");
		for (const type of [
			"order_submitted",
			"cancel_requested",
			"order_terminal",
		]) {
			const html = renderFact(type);
			expect(html).not.toContain("受控成交来源");
			expect(html).not.toContain("取得时间（北京时间）");
		}
	});
});
