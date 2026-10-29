import { describe, expect, test } from "bun:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MarketSessionFacts } from "../src/MarketSession";
import { marketSessionSchema } from "../src/marketSessionSchema";

const current = {
	start: "2026-10-29T09:30:00+08:00",
	end: "2026-10-29T11:30:00+08:00",
};
const next = {
	start: "2026-10-29T13:00:00+08:00",
	end: "2026-10-29T14:57:00+08:00",
};
const base = {
	market: "CN",
	as_of: "2026-10-29T10:00:00+08:00",
	market_timezone: "Asia/Shanghai",
	market_date: "2026-10-29",
	phase: "supported",
	current_period: current,
	next_period: next,
	reason: null,
};

describe("时段响应边界", () => {
	test("完整对象、额外字段和等价偏移可以读取", () => {
		expect(
			marketSessionSchema.parse({
				...base,
				extra: "ignored",
				as_of: "2026-10-29T02:00:00Z",
			}),
		).toEqual({ ...base, as_of: "2026-10-29T02:00:00Z" });
	});
	test("下一段未知保留当前事实", () => {
		expect(
			marketSessionSchema.parse({ ...base, next_period: null }).phase,
		).toBe("supported");
	});
	test("纽约日期允许与北京时间日期不同", () => {
		expect(
			marketSessionSchema.safeParse({
				...base,
				market: "US",
				market_timezone: "America/New_York",
				market_date: "2026-10-29",
				as_of: "2026-10-30T00:30:00+08:00",
				current_period: {
					start: "2026-10-29T13:30:00Z",
					end: "2026-10-29T20:00:00Z",
				},
				next_period: null,
			}).success,
		).toBe(true);
	});
	test.each([
		{ market: "XX" },
		{ market_timezone: "Asia/Hong_Kong" },
		{ market_date: "2026-10-28" },
		{ market_date: "2026-02-30" },
		{ phase: "closed" },
		{ phase: "unknown" },
		{ current_period: null },
		{ reason: "calendar_unavailable" },
		{ reason: "failure" },
		{ as_of: "invalid" },
		{ as_of: "2026-10-29T10:00:00" },
		{ as_of: "2026-02-30T10:00:00+08:00" },
		{ current_period: { ...current, start: current.end } },
		{ current_period: { ...current, end: base.as_of } },
		{ current_period: { ...current, start: "2026-10-29T10:00:01+08:00" } },
		{ next_period: { ...next, start: base.as_of } },
		{ next_period: { ...next, start: "2026-10-29T10:30:00+08:00" } },
		{ next_period: { ...next, end: next.start } },
		{ phase: "break" },
		{ phase: "unknown", current_period: null, reason: "calendar_unavailable" },
	])("拒绝矛盾对象 %j", (patch) => {
		expect(marketSessionSchema.safeParse({ ...base, ...patch }).success).toBe(
			false,
		);
	});
	test("整体未知无时段且原因明确", () => {
		expect(
			marketSessionSchema.parse({
				...base,
				phase: "unknown",
				current_period: null,
				next_period: null,
				reason: "calendar_unavailable",
			}).phase,
		).toBe("unknown");
	});
});

describe("实际组件文案", () => {
	test("北京时间精确到秒且下一段不是当前开盘", () => {
		const html = renderToStaticMarkup(
			createElement(MarketSessionFacts, {
				value: marketSessionSchema.parse(base),
			}),
		);
		expect(html).toContain("2026-10-29 10:00:00");
		expect(html).toContain("本段结束 2026-10-29 11:30:00");
		expect(html).toContain(
			"下一支持时段 2026-10-29 13:00:00 至 2026-10-29 14:57:00",
		);
	});
	test.each([
		"before_open",
		"break",
		"unsupported_closing",
		"after_hours",
		"non_session_day",
		"unknown",
	])("phase %s 不显示旧当前区间", (phase) => {
		const value = marketSessionSchema.parse({
			...base,
			phase,
			current_period: null,
			next_period: null,
			reason: phase === "unknown" ? "calendar_unavailable" : null,
		});
		const html = renderToStaticMarkup(
			createElement(MarketSessionFacts, { value }),
		);
		expect(html).not.toContain("本段结束");
		expect(html).not.toContain("支持时段内");
		if (phase === "unknown") expect(html).toContain("市场时段暂不可用");
		else expect(html).toContain("下一支持时段暂不可用");
		if (phase === "unsupported_closing")
			expect(html).toContain("参考日实际收盘");
	});
});

test("微秒边界不截断：未来当前段拒绝，严格未来下一段接受，等价偏移相等", () => {
	const value = {
		...base,
		as_of: "2026-10-29T02:00:00.000000Z",
		current_period: { start: "2026-10-29T02:00:00.000001Z", end: current.end },
		next_period: null,
	};
	expect(marketSessionSchema.safeParse(value).success).toBe(false);
	const tinyCurrent = {
		start: "2026-10-29T01:59:59.999999Z",
		end: "2026-10-29T02:00:00.000001Z",
	};
	const future = {
		start: "2026-10-29T02:00:00.000001Z",
		end: "2026-10-29T02:00:00.000002Z",
	};
	expect(
		marketSessionSchema.safeParse({
			...value,
			current_period: tinyCurrent,
			next_period: future,
		}).success,
	).toBe(true);
	expect(
		marketSessionSchema.safeParse({
			...value,
			as_of: "2026-10-29T10:00:00.000001+08:00",
		}).success,
	).toBe(true);
	expect(
		marketSessionSchema.safeParse({
			...value,
			current_period: tinyCurrent,
			next_period: { ...future, start: "2026-10-29T10:00:00.000000+08:00" },
		}).success,
	).toBe(false);
});
