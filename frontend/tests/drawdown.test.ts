import { expect, test } from "bun:test";
import {
	DRAWDOWN_BROKEN_NOTE,
	drawdownDayLabel,
	drawdownPointLabel,
	drawdownSchema,
	drawdownSummary,
	snapshotRangeSchema,
} from "../src/history";

const drawdown = drawdownSchema.parse({
	status: "computed",
	reason: "受控回落说明",
	peak: {
		day: "2026-11-20",
		captured_at: "2026-11-20T09:31:00+08:00",
		total_assets_cny: "10000",
	},
	trough: {
		day: "2026-11-24",
		captured_at: "2026-11-24T10:02:00+08:00",
		total_assets_cny: "8750",
	},
	amount_cny: "1250",
	percent: "12.50",
	segments_used: 1,
	broken_days: 0,
});

test("回落端点日期同年用 MM-DD，跨年补全为 YYYY-MM-DD", () => {
	expect(drawdownDayLabel("2026-11-20", "2026-11-24")).toBe("11-20");
	expect(drawdownDayLabel("2026-11-24", "2026-11-20")).toBe("11-24");
	expect(drawdownDayLabel("2025-12-30", "2026-01-02")).toBe("2025-12-30");
	expect(drawdownDayLabel("2026-01-02", "2025-12-30")).toBe("2026-01-02");
	expect(drawdownDayLabel("2026-11-20", null)).toBe("11-20");
});

test("回落端点附带北京时间记录时间", () => {
	expect(
		drawdownPointLabel(
			{
				day: "2026-11-20",
				captured_at: "2026-11-20T09:31:00+08:00",
			},
			"2026-11-24",
		),
	).toBe("11-20 09:31");
	expect(
		drawdownPointLabel(
			{
				day: "2025-12-30",
				captured_at: "2025-12-30T01:31:00+08:00",
			},
			"2026-01-02",
		),
	).toBe("2025-12-30 01:31");
});

test("computed 摘要包含比例、金额、两端日期与金额", () => {
	expect(drawdownSummary(drawdown)).toBe(
		"区间内最大回落 −12.50%（¥1,250.00）：从 11-20 09:31 的 ¥10,000.00 回落到 11-24 10:02 的 ¥8,750.00",
	);
});

test("跨年区间两端日期用完整年份", () => {
	expect(
		drawdownSummary(
			drawdownSchema.parse({
				...drawdown,
				peak: {
					day: "2025-12-30",
					captured_at: "2025-12-30T09:31:00+08:00",
					total_assets_cny: "10000",
				},
				trough: {
					day: "2026-01-02",
					captured_at: "2026-01-02T10:02:00+08:00",
					total_assets_cny: "7500",
				},
				amount_cny: "2500",
				percent: "25.00",
				broken_days: 1,
			}),
		),
	).toBe(
		"区间内最大回落 −25.00%（¥2,500.00）：从 2025-12-30 09:31 的 ¥10,000.00 回落到 2026-01-02 10:02 的 ¥7,500.00",
	);
	expect(DRAWDOWN_BROKEN_NOTE).toContain("只在连续完整记录内计算");
});

test("no_drop 与 insufficient 的固定文案", () => {
	expect(
		drawdownSummary(
			drawdownSchema.parse({
				...drawdown,
				status: "no_drop",
				peak: null,
				trough: null,
				amount_cny: null,
				percent: null,
			}),
		),
	).toBe("区间内连续完整记录中没有出现回落");
	expect(
		drawdownSummary(
			drawdownSchema.parse({
				...drawdown,
				status: "insufficient",
				peak: null,
				trough: null,
				amount_cny: null,
				percent: null,
			}),
		),
	).toBe("连续完整记录不足两天，无法计算回落");
});

test("computed 但字段缺失时退回后端白话说明", () => {
	expect(
		drawdownSummary(
			drawdownSchema.parse({
				...drawdown,
				status: "computed",
				peak: null,
				trough: null,
			}),
		),
	).toBe("受控回落说明");
});

test("区间接口缺 drawdown 字段时仍可解析（旧后端兼容）", () => {
	const base = {
		round_id: "r1",
		start_date: null,
		end_date: null,
		items: [],
		recorded_days: 0,
		complete_days: 0,
		missing_days: 0,
		first: null,
		last: null,
		change_cny: null,
		continuous: true,
		reason: "受控说明",
	};
	expect(snapshotRangeSchema.parse(base).drawdown).toBeUndefined();
	expect(
		snapshotRangeSchema.parse({ ...base, drawdown }).drawdown?.status,
	).toBe("computed");
});
