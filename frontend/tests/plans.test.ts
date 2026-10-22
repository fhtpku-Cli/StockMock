import { describe, expect, it } from "bun:test";
import { alertPageSchema, planSchema, reviewSchema } from "../src/planSchema";

const plan = {
	id: "controlled-plan",
	round_id: "controlled-round",
	symbol: "510300.SS",
	currency: "CNY",
	direction: "watch",
	status: "active",
	reason: "受控边界数据",
	target_price: "101.1234",
	stop_price: null,
	review_date: "2026-10-23",
	invalidation: "",
	version: 2,
	created_at: "2026-10-23T02:00:00Z",
	updated_at: "2026-10-23T02:05:00Z",
	monitoring: {
		status: "unavailable",
		message: "来源失败，不能判断",
		checked_at: "2026-10-23T02:06:00Z",
		market_time: null,
		price: null,
		source: null,
	},
};

describe("计划与复盘接口边界", () => {
	it("保留精确原币价格与不可判断的监控状态，不补造报价", () => {
		const parsed = planSchema.parse(plan);
		expect(parsed.target_price).toBe("101.1234");
		expect(parsed.monitoring.status).toBe("unavailable");
		expect(parsed.monitoring.price).toBeNull();
	});
	it("拒绝没有并发版本及未知计划状态的响应", () => {
		expect(planSchema.safeParse({ ...plan, version: undefined }).success).toBe(
			false,
		);
		expect(planSchema.safeParse({ ...plan, status: "cancelled" }).success).toBe(
			false,
		);
	});
	it("金额接口不能用浮点数字代替精确字符串", () => {
		expect(
			planSchema.safeParse({ ...plan, target_price: 101.1234 }).success,
		).toBe(false);
	});
	it("未读总数与当前分页分开保留", () => {
		const parsed = alertPageSchema.parse({
			items: [],
			total: 0,
			offset: 0,
			limit: 10,
			unread_count: 35,
		});
		expect(parsed.unread_count).toBe(35);
		expect(parsed.total).toBe(0);
	});
	it("部分卖出保留两种数量与原币已实现收益，不推断计划结束", () => {
		const parsed = reviewSchema.parse({
			id: "review",
			plan,
			legacy_order: null,
			notes: [],
			order_count: 2,
			filled_count: 2,
			bought_quantity: 200,
			sold_quantity: 100,
			remaining_quantity: 100,
			account_quantity: 300,
			realized_pnl: "989.50",
			currency: "CNY",
			status: "pending",
			actual_status: "部分卖出，仍有持仓",
		});
		expect(parsed.plan?.status).toBe("active");
		expect(parsed.remaining_quantity).toBe(100);
		expect(parsed.account_quantity).toBe(300);
		expect(parsed.realized_pnl).toBe("989.50");
	});
});
