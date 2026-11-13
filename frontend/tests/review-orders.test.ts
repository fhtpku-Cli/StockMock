import { describe, expect, it } from "bun:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import {
	ReviewOrderSummary,
	ReviewOrders,
	sortReviewOrders,
} from "../src/ReviewOrders";
import { type Order, orderSchema } from "../src/schema";

const order = orderSchema.parse({
	id: "order-0001",
	request_id: "request-1",
	symbol: "AAPL",
	side: "buy",
	order_type: "limit",
	quantity: 2,
	limit_price: "999",
	price_bound: "999",
	status: "accepted",
	status_reason: "",
	created_at: "2025-12-31T16:00:00Z",
	active_at: "2026-01-01T14:00:00Z",
	expires_at: "2026-01-02T20:00:00Z",
	cancel_at: null,
	filled_at: null,
	confirmed_at: null,
	fill_price: null,
	fee: null,
	currency: "USD",
	reserved_cny: "0",
	reserved_foreign: "1998",
	fx_rate: null,
	fx_source: null,
	fx_time: null,
	reason: "受控复盘委托",
	target_price: null,
	stop_price: null,
});

describe("复盘关联委托概览", () => {
	it("同一毫秒内仍按提交微秒排序，只有完整时刻相等才按编号", () => {
		// Given: 后端原始时刻相差一微秒，编号顺序与时间相反。
		const orders = [
			{ ...order, id: "a-later", created_at: "2026-01-01T00:00:00.000002Z" },
			{
				...order,
				id: "z-earlier",
				created_at: "2026-01-01T08:00:00.000001+08:00",
			},
		];
		// When: 排序完整原始时刻。
		const ids = sortReviewOrders(orders).map((item) => item.id);
		// Then: 微秒不会被抹平。
		expect(ids).toEqual(["z-earlier", "a-later"]);
	});

	it("按真实提交时刻及完整编号排序，不改变原数组", () => {
		// Given: 输入倒序，等价时刻使用不同偏移，短编号相同。
		const input = [
			{ ...order, id: "same-id-z", created_at: "2026-01-01T01:00:00+08:00" },
			{ ...order, id: "same-id-a", created_at: "2025-12-31T17:00:00Z" },
			{ ...order, id: "earlier" },
		];
		// When: 生成阅读顺序。
		const result = sortReviewOrders(input);
		// Then: 最早在前，同刻比较完整编号，源数据保持。
		expect(result.map((item) => item.id)).toEqual([
			"earlier",
			"same-id-a",
			"same-id-z",
		]);
		expect(input.map((item) => item.id)).toEqual([
			"same-id-z",
			"same-id-a",
			"earlier",
		]);
	});

	it("跨年时间显示北京时间年份，成交价保留原小数位", () => {
		// Given: 六位成交价和与提交时间不同的市场成交时间。
		const filled: Order = {
			...order,
			status: "filled",
			fill_price: "1.234567",
			filled_at: "2026-01-01T16:00:00Z",
		};
		// When: 实际渲染用户可见摘要。
		const html = renderToStaticMarkup(
			createElement(ReviewOrderSummary, { order: filled }),
		);
		// Then: 两个时刻不混用，价格不截成两位。
		expect(html).toContain("2026-01-01 00:00:00");
		expect(html).toContain("2026-01-02 00:00:00");
		expect(html).toContain("北京时间");
		expect(html).toContain("1.234567");
		expect(html).toContain("USD");
	});

	it("已成交缺价格时间时保留未知，不借限价或确认时间补造", () => {
		// Given: 旧成交仅有系统确认时间和限价。
		const historical: Order = {
			...order,
			status: "filled",
			confirmed_at: "2026-01-02T18:12:00Z",
		};
		// When: 读取摘要。
		const html = renderToStaticMarkup(
			createElement(ReviewOrderSummary, { order: historical }),
		);
		// Then: 仍是成交，两个成交字段未知。
		expect(html).toContain("已成交");
		expect(html.match(/<dd>未记录<\/dd>/g)).toHaveLength(2);
		expect(html).not.toContain("尚未成交");
		expect(html).not.toContain("999");
		expect(html).not.toContain("02:12:00");
	});

	it.each([
		["accepted", "等待成交"],
		["cancel_pending", "撤单确认中"],
		["cancelled", "已撤单"],
		["expired", "已过期"],
		["terminated", "已终止"],
	] as const)("%s 委托使用已保存状态且不显示成交价格", (status, label) => {
		// Given: 状态非成交，另有撤单请求，不以请求改写终态。
		const waiting: Order = {
			...order,
			status,
			cancel_at: "2026-01-01T15:00:00Z",
		};
		// When: 渲染概览。
		const html = renderToStaticMarkup(
			createElement(ReviewOrderSummary, { order: waiting }),
		);
		// Then: 不把委托价或撤单请求当成交/终态。
		expect(html).toContain(label);
		expect(html).toContain("尚未成交");
		expect(html).not.toContain("成交价格");
	});

	it("有版本号却缺快照时不补造当时计划", () => {
		// Given: 旧委托只保留计划版本号，缺少快照。
		const historical: Order = {
			...order,
			plan_version: 7,
			plan_snapshot: null,
		};
		// When: 渲染摘要。
		const html = renderToStaticMarkup(
			createElement(ReviewOrderSummary, { order: historical }),
		);
		// Then: 明确历史缺失，不拿版本号冒充快照。
		expect(html).toContain("当时计划未保存");
		expect(html).not.toContain("第 7 版");
	});

	it("无委托保留观察结论提示且不生成选择入口", () => {
		// Given / When: 完整接口返回空集合，渲染概览。
		const html = renderToStaticMarkup(
			createElement(ReviewOrders, { orders: [], current: null }),
		);
		// Then: 不虚构委托或强制选择。
		expect(html).toContain("尚无关联委托，也可以主动记录观察结论");
		expect(html).not.toContain("aria-pressed");
	});

	it("一笔无需点击就展示原详情和缺快照解释", () => {
		// Given / When: 首次打开仅一笔的旧委托。
		const html = renderToStaticMarkup(
			createElement(ReviewOrders, { orders: [order], current: null }),
		);
		// Then: 默认选中并展示真实详情，保留不能补造历史的解释。
		expect(html).toContain('aria-pressed="true"');
		expect(html).toContain('data-order-id="order-0001"');
		expect(html).toContain("不能用当前计划补造历史");
	});

	it("508笔首次展示20笔且默认选择排序后的第一笔", () => {
		// Given: 接口包含倒序完整集合。
		const orders = Array.from({ length: 508 }, (_, index) => ({
			...order,
			id: `order-${String(507 - index).padStart(4, "0")}`,
		}));
		// When: 初次渲染概览。
		const html = renderToStaticMarkup(
			createElement(ReviewOrders, { orders, current: null }),
		);
		// Then: 总数不裁剪，并保留继续阅读入口。
		expect(html.match(/data-review-order-id=/g)).toHaveLength(20);
		expect(html).toContain("508 笔");
		expect(html).toContain('data-order-id="order-0000"');
		expect(html).toContain("加载更多");
	});
});
