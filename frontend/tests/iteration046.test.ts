import { describe, expect, test } from "bun:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { ActiveOrdersSummary } from "../src/ActiveOrdersSummary";
import { SellabilityDetails } from "../src/SellabilityDetails";
import { portfolioSchema } from "../src/schema";

const position = {
	symbol: "510300.SS",
	name: "受控持仓",
	market: "CN",
	currency: "CNY",
	quantity: 500,
	sellable: 200,
	reserved: 100,
	average_cost: "4.1891",
	cost: "2094.55",
	market_price: null,
	market_value: null,
	pnl: null,
	valuation_status: "missing",
	market_value_cny: null,
	weight_percent: null,
	pnl_percent: null,
	price_time: null,
};
const portfolio = {
	round_id: "controlled-round",
	initial_capital: "1000000.00",
	total_assets_cny: null,
	pnl_cny: null,
	valuation_status: "missing",
	cash: [],
	positions: [position],
	worker: { last_run: null, status: "starting" },
	warnings: ["受控缺失行情"],
};
const active = { accepted_count: 2, cancel_pending_count: 1 };
const locked = {
	locked_quantity: 200,
	next_unlock_at: "2026-10-30T01:30:00Z",
	next_unlock_quantity: 100,
};
const unlocked = {
	locked_quantity: 0,
	next_unlock_at: null,
	next_unlock_quantity: 0,
};
const response = (activeOrders: unknown, sellability: unknown) => ({
	...portfolio,
	active_orders: activeOrders,
	positions: [{ ...position, sellability }],
});

describe("0.4.6 响应兼容与拒绝边界", () => {
	test.each([undefined, null])("旧对象 %p 保持未知并保留原可卖量", (value) => {
		const parsed = portfolioSchema.parse(response(value, value));
		expect(parsed.active_orders).toBe(value);
		expect(parsed.positions[0]?.sellability).toBe(value);
		expect(parsed.positions[0]?.sellable).toBe(200);
	});
	test("真正移除新增键的旧响应可以读取", () => {
		const parsed = portfolioSchema.parse(portfolio);
		expect(parsed.active_orders).toBeUndefined();
		expect(parsed.positions[0]?.sellability).toBeUndefined();
	});
	test("有效零完整保留，不能与未知混淆", () => {
		const zero = { accepted_count: 0, cancel_pending_count: 0 };
		const parsed = portfolioSchema.parse(response(zero, unlocked));
		expect(parsed.active_orders).toEqual(zero);
		expect(parsed.positions[0]?.sellability).toEqual(unlocked);
	});
	test("无行情时仍保留完整非零数量投影", () => {
		const parsed = portfolioSchema.parse(response(active, locked));
		expect(parsed.active_orders).toEqual(active);
		expect(parsed.positions[0]?.sellability).toEqual(locked);
		expect(parsed.total_assets_cny).toBeNull();
	});
	test.each([
		{},
		{ accepted_count: 1 },
		{ ...active, accepted_count: -1 },
		{ ...active, cancel_pending_count: -1 },
		{ ...active, accepted_count: 1.5 },
		{ ...active, accepted_count: "2" },
		{ ...active, cancel_pending_count: null },
	])("摘要错误对象 %j 被整份响应拒绝", (invalid) => {
		expect(portfolioSchema.safeParse(response(invalid, locked)).success).toBe(
			false,
		);
	});
	test.each([
		{},
		{ locked_quantity: 200, next_unlock_at: locked.next_unlock_at },
		{ ...locked, locked_quantity: -1 },
		{ ...locked, locked_quantity: "200" },
		{ ...locked, next_unlock_quantity: -1 },
		{ ...locked, next_unlock_quantity: 0.5 },
		{ ...locked, next_unlock_quantity: "100" },
		{ ...locked, next_unlock_quantity: 0 },
		{ ...locked, next_unlock_quantity: 201 },
		{ ...locked, next_unlock_at: null },
		{ ...locked, next_unlock_at: "2026-10-30T09:30:00" },
		{ ...locked, next_unlock_at: "2026-02-30T09:30:00+08:00" },
		{ ...unlocked, next_unlock_at: locked.next_unlock_at },
		{ ...unlocked, next_unlock_quantity: 1 },
	])("解锁错误对象 %j 被整份响应拒绝", (invalid) => {
		expect(portfolioSchema.safeParse(response(active, invalid)).success).toBe(
			false,
		);
	});
});

describe("0.4.6 数量与时间的实际组件输出", () => {
	test.each([undefined, null])("摘要 %p 显示未知但保留入口", (value) => {
		const html = renderToStaticMarkup(
			createElement(ActiveOrdersSummary, { value, onOrders: () => {} }),
		);
		expect(html).toContain("委托摘要暂不可用");
		expect(html).not.toContain("暂无进行中委托");
		expect(html).toContain("查看进行中委托");
	});
	test("真实零摘要仍有进行中入口", () => {
		const html = renderToStaticMarkup(
			createElement(ActiveOrdersSummary, {
				value: { accepted_count: 0, cancel_pending_count: 0 },
				onOrders: () => {},
			}),
		);
		expect(html).toContain("暂无进行中委托");
		expect(html).toContain("查看进行中委托");
	});
	test("摘要合并两种活动状态而不丢撤单待确认", () => {
		const html = renderToStaticMarkup(
			createElement(ActiveOrdersSummary, { value: active, onOrders: () => {} }),
		);
		expect(html).toContain("进行中委托3笔");
		expect(html).toContain("已受理2 · 撤单待确认1");
	});
	test("混合多批次显示下一批实际数量和单独占用说明", () => {
		const html = renderToStaticMarkup(
			createElement(SellabilityDetails, { value: locked, reserved: 100 }),
		);
		expect(html).toContain("待解锁200股；下一批100股");
		expect(html).toContain("2026-10-30 09:30:00");
		expect(html).toContain("其余100股属于后续批次");
		expect(html).toContain("申请撤单不会立即释放");
	});
	test("带偏移的跨日时刻显示北京时间完整日期和秒", () => {
		const html = renderToStaticMarkup(
			createElement(SellabilityDetails, {
				value: { ...locked, next_unlock_at: "2026-10-29T12:00:01-04:00" },
				reserved: 0,
			}),
		);
		expect(html).toContain("2026-10-30 00:00:01");
	});
	test("全部可卖时没有解锁或占用警告", () => {
		const html = renderToStaticMarkup(
			createElement(SellabilityDetails, { value: unlocked, reserved: 0 }),
		);
		expect(html).not.toContain("<small>");
	});
	test("纯占用不套用解锁时刻", () => {
		const html = renderToStaticMarkup(
			createElement(SellabilityDetails, { value: unlocked, reserved: 100 }),
		);
		expect(html).not.toContain("预计于");
		expect(html).toContain("申请撤单不会立即释放");
	});
	test("旧响应解锁未知仍明确解释真实卖单占用", () => {
		const html = renderToStaticMarkup(
			createElement(SellabilityDetails, { value: undefined, reserved: 100 }),
		);
		expect(html).toContain("解锁信息暂不可用");
		expect(html).toContain("申请撤单不会立即释放");
	});
});
