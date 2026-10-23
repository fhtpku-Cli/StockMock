import { expect, test } from "bun:test";
import {
	compareDecimal,
	dateRangeError,
	filterParams,
	type Position,
	sortPositions,
} from "../src/analysis";
import { snapshotRangeSchema } from "../src/history";
import {
	cashStatementSchema,
	ledgerKindValue,
	ledgerPageSchema,
} from "../src/statementSchema";

const position = (symbol: string, value: string | null): Position => ({
	symbol,
	name: symbol,
	market: "US",
	currency: "USD",
	quantity: 1,
	sellable: 1,
	reserved: 0,
	average_cost: "1",
	cost: "1",
	market_price: "1",
	market_value: "1",
	pnl: "0",
	valuation_status: "参考估值",
	market_value_cny: value,
	weight_percent: value,
	pnl_percent: value,
	price_time: null,
});
test("三种排序缺失最后、相同数值按代码稳定且不更改原账户分母", () => {
	const rows = [
		position("Z", "12.0"),
		position("M", null),
		position("A", "12"),
		position("B", "13"),
		position("C", "-2"),
	];
	for (const sort of [
		"market_value_cny",
		"weight_percent",
		"pnl_percent",
	] as const) {
		expect(sortPositions(rows, sort).map((row) => row.symbol)).toEqual([
			"B",
			"A",
			"Z",
			"C",
			"M",
		]);
	}
	expect(rows[0]?.symbol).toBe("Z");
	expect(
		sortPositions(
			rows.filter((row) => row.symbol === "A"),
			"weight_percent",
		)[0]?.weight_percent,
	).toBe("12");
});
test("金额排序不受浮点精度丢失影响", () => {
	expect(compareDecimal("10000000000000000.01", "10000000000000000.02")).toBe(
		-1,
	);
	expect(compareDecimal("-0.01", "0")).toBe(-1);
	expect(compareDecimal("-2.10", "-2.1")).toBe(0);
});
test("日期拒绝倒置和非法自然日，允许开放资金范围", () => {
	expect(
		dateRangeError({ start_date: "2026-02-30", end_date: "" }),
	).not.toBeNull();
	expect(
		dateRangeError({ start_date: "2026-10-23", end_date: "2026-10-22" }),
	).not.toBeNull();
	expect(
		dateRangeError({ start_date: "2026-10-22", end_date: "2026-10-22" }),
	).toBeNull();
	expect(dateRangeError({ start_date: "", end_date: "2026-10-22" })).toBeNull();
	expect(
		filterParams({ start_date: "2026-10-22", end_date: "", kind: "旧 类型" }),
	).toBe("start_date=2026-10-22&kind=%E6%97%A7+%E7%B1%BB%E5%9E%8B");
});
test("分页必须携带追加边界与轮次，不能静默变成完整列表", () => {
	expect(
		ledgerPageSchema.safeParse({ items: [], total: 510, offset: 0, limit: 50 })
			.success,
	).toBe(false);
	expect(
		ledgerPageSchema.parse({
			items: [],
			total: 510,
			offset: 500,
			limit: 50,
			boundary: 510,
			round_id: "r",
		}).total,
	).toBe(510);
});
test("缺失的实际末快照端点保留，摘要不能强行补零", () => {
	const last = {
		id: "last",
		round_id: "r",
		day: "2026-10-23",
		captured_at: "2026-10-23T02:00:00Z",
		total_assets_cny: null,
		pnl_cny: null,
		status: "missing",
		issues: ["缺汇率"],
		price_times: {},
	};
	const result = snapshotRangeSchema.parse({
		round_id: "r",
		start_date: "2026-10-22",
		end_date: "2026-10-23",
		items: [last],
		recorded_days: 1,
		complete_days: 0,
		missing_days: 1,
		first: last,
		last,
		change_cny: null,
		continuous: false,
		reason: "端点不完整",
	});
	expect(result.last?.status).toBe("missing");
	expect(result.change_cny).toBeNull();
	expect(
		snapshotRangeSchema.safeParse({ ...result, change_cny: 0 }).success,
	).toBe(false);
});
test("资金核对必须保留每币种差额与所选类型变化的精确金额", () => {
	const row = {
		currency: "CNY",
		initial_cash: "1000000",
		total_inflow: "10",
		total_outflow: "11",
		calculated_balance: "999999",
		ledger_balance: "1000000",
		difference: "1",
		opening_balance: "1000000",
		period_inflow: "10",
		period_outflow: "11",
		period_net_change: "-1",
		closing_balance: "999999",
		selected_net_change: "10",
	};
	const value = {
		round_id: "r",
		start_date: null,
		end_date: null,
		boundary: 510,
		currencies: [row],
	};
	expect(cashStatementSchema.parse(value).currencies[0]?.difference).toBe("1");
	expect(
		cashStatementSchema.safeParse({
			...value,
			currencies: [{ ...row, selected_net_change: 10 }],
		}).success,
	).toBe(false);
});

test("流水中文类型映射兼容原代码和未知旧类型", () => {
	expect(ledgerKindValue("拆股 / 合股")).toBe("split");
	expect(ledgerKindValue("split")).toBe("split");
	expect(ledgerKindValue("应收股息到账")).toBe("dividend_payment");
	expect(ledgerKindValue("legacy_unknown")).toBe("legacy_unknown");
});
