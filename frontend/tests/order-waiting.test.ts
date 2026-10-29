import { describe, expect, test } from "bun:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { OrderRecord } from "../src/OrderRecord";
import { orderSchema } from "../src/schema";

const order = {
	id: "order-waiting",
	request_id: "request-waiting",
	symbol: "AAPL",
	side: "buy",
	order_type: "limit",
	quantity: 1,
	limit_price: "100",
	price_bound: "100",
	status: "accepted",
	status_reason: "旧服务等待说明",
	created_at: "2026-10-30T13:30:00Z",
	active_at: "2026-10-30T13:30:00Z",
	expires_at: "2026-10-30T20:00:00Z",
	cancel_at: null,
	filled_at: null,
	confirmed_at: null,
	fill_price: null,
	fee: null,
	currency: "USD",
	reserved_cny: "800",
	reserved_foreign: "0",
	fx_rate: "7",
	fx_source: "受控测试",
	fx_time: "2026-10-30T00:00:00Z",
	reason: "受控测试",
	target_price: null,
	stop_price: null,
};
const waiting = {
	code: "checked_not_met",
	message: "本系统已处理至 04:00，尚未满足成交条件；不代表市场全部成交。",
	checked_through: "2026-10-30T16:00:00-04:00",
	since: null,
};
const render = (extra: Readonly<Record<string, unknown>>) =>
	renderToStaticMarkup(
		createElement(OrderRecord, {
			order: orderSchema.parse({ ...order, ...extra }),
		}),
	);

describe("活动委托等待说明", () => {
	test.each([
		"paused",
		"source_failing",
		"after_cutoff",
		"not_active",
		"awaiting_first",
		"checked_not_met",
	])("解析服务端分类 %s 并保留已处理时间", (code) => {
		const response = { ...order, waiting: { ...waiting, code } };
		const parsed = orderSchema.parse(response);
		expect(parsed.waiting).toEqual(response.waiting);
	});
	test.each([{}, { waiting: null }])("旧响应沿用持久化原因 %j", (extra) => {
		const html = render(extra);
		expect(html).toContain(order.status_reason);
		expect(html).not.toContain("本系统已处理至");
	});
	test.each(["accepted", "cancel_pending"])(
		"%s 显示服务端说明及跨日完整北京时间",
		(status) => {
			const html = render({ status, waiting });
			expect(html).toContain(waiting.message);
			expect(html).toContain("2026-10-31 04:00:00");
			expect(html).toContain("北京时间");
			expect(html).not.toContain(order.status_reason);
		},
	);
	test("终态即使误带 waiting 也保持历史结果说明", () => {
		const html = render({ status: "expired", waiting });
		expect(html).toContain(order.status_reason);
		expect(html).not.toContain(waiting.message);
		expect(html).not.toContain("2026-10-31 04:00:00");
	});
	test("尚未生效的分类遵从服务端，不按浏览器当前时间重判", () => {
		const response = {
			code: "not_active",
			message: "服务端确认尚未生效",
			checked_through: null,
			since: "2000-01-01T00:00:00Z",
		};
		const html = render({
			active_at: "2000-01-01T00:00:00Z",
			waiting: response,
		});
		expect(html).toContain(response.message);
		expect(html).not.toContain("本系统已处理至");
	});
	test.each([
		{ ...waiting, code: "unknown" },
		{ ...waiting, checked_through: "not-a-time" },
		{ ...waiting, since: "2026-10-30T12:00:00" },
	])("拒绝不可解释的等待信息 %j", (value) => {
		expect(orderSchema.safeParse({ ...order, waiting: value }).success).toBe(
			false,
		);
	});
});
