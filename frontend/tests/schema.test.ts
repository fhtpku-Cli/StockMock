import { describe, it, expect } from "bun:test";
import { portfolioSchema, quoteSchema, orderSchema } from "../src/schema";
import { money, price } from "../src/api";
describe("接口边界拒绝不可信的账户与行情", () => {
	it("缺失真实总资产时不能用默认本金补齐", () => {
		const valid = {
			round_id: "round-test",
			initial_capital: "1000000.00",
			total_assets_cny: null,
			pnl_cny: null,
			valuation_status: "行情缺失",
			cash: [
				{
					currency: "CNY",
					balance: "1000000.00",
					reserved: "0.00",
					available: "1000000.00",
				},
			],
			positions: [],
			worker: { last_run: null, status: "starting" },
			warnings: ["受控测试：估值缺失"],
		};
		expect(portfolioSchema.safeParse(valid).success).toBe(true);
		const { total_assets_cny: missingValue, ...withoutTotal } = valid;
		expect(missingValue).toBe(null);
		expect(portfolioSchema.safeParse(withoutTotal).success).toBe(false);
		expect(money(null)).toBe("—");
	});
	it("行情价格必须为十进制字符串，不能接受数字或非法价格", () => {
		const quote = {
			symbol: "AAPL",
			price: "100.01",
			currency: "USD",
			market_time: "2026-10-22T01:00:00Z",
			retrieved_at: "2026-10-22T01:01:00Z",
			source: "受控测试",
			status: "reference",
			message: "延迟参考价",
		};
		expect(quoteSchema.safeParse(quote).success).toBe(true);
		expect(quoteSchema.safeParse({ ...quote, price: 100.01 }).success).toBe(
			false,
		);
		expect(quoteSchema.safeParse({ ...quote, price: "NaN" }).success).toBe(
			false,
		);
	});
	it("未知订单状态不能默认为已成交", () => {
		const valid = {
			id: "order",
			request_id: "request-123",
			symbol: "510300.SS",
			side: "buy",
			order_type: "limit",
			quantity: 100,
			limit_price: "4.189",
			price_bound: "4.189",
			status: "accepted",
			status_reason: "等待成交",
			created_at: "2026-10-22T02:00:00Z",
			active_at: "2026-10-22T02:00:00Z",
			expires_at: "2026-10-22T07:00:00Z",
			cancel_at: null,
			filled_at: null,
			confirmed_at: null,
			fill_price: null,
			fee: null,
			currency: "CNY",
			reserved_cny: "419.11",
			reserved_foreign: "0",
			fx_rate: "1",
			fx_source: "",
			fx_time: null,
			reason: "受控测试",
			target_price: null,
			stop_price: null,
		};
		expect(orderSchema.safeParse(valid).success).toBe(true);
		expect(orderSchema.safeParse({ ...valid, status: "unknown" }).success).toBe(
			false,
		);
	});
});

describe("价格精度与现金金额分离", () => {
	it("ETF 保留 0.001 步长；资金仍为两位", () => {
		expect(price("4.189", "CNY", "0.001")).toContain("4.189");
		expect(money("4.189")).toContain("4.19");
		expect(price("100.2", "USD", "0.01")).toContain("100.20");
		expect(price("4.1911", "CNY")).toContain("4.1911");
	});
});
