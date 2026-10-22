import { expect, test } from "bun:test";
import { snapshotSegments, snapshotSchema } from "../src/history";
import { recordMatches } from "../src/RecordFilters";
const point = (
	day: string,
	status: "complete" | "missing" | "stale" = "complete",
) =>
	snapshotSchema.parse({
		id: day,
		round_id: "round",
		day,
		captured_at: `${day}T00:00:00Z`,
		total_assets_cny: status === "missing" ? null : "1000000",
		pnl_cny: status === "missing" ? null : "0",
		status,
		issues: [],
		price_times: {},
	});
test("缺失、过时、未记录日期都不能跨越连线", () => {
	const input = [
		point("2026-10-01"),
		point("2026-10-02"),
		point("2026-10-03", "missing"),
		point("2026-10-04"),
		point("2026-10-05", "stale"),
		point("2026-10-06"),
		point("2026-10-08"),
	];
	expect(
		snapshotSegments(input).map((group) => group.map((p) => p.day)),
	).toEqual([
		["2026-10-01", "2026-10-02"],
		["2026-10-04"],
		["2026-10-06"],
		["2026-10-08"],
	]);
	expect(snapshotSegments([point("2026-10-01", "missing")])).toEqual([]);
});
test("日期筛选采用北京时间并区分未关联记录", () => {
	const filter = { from: "2026-10-23", to: "2026-10-23", market: "US" };
	expect(recordMatches("2026-10-22T16:30:00Z", "USD", filter)).toBe(true);
	expect(recordMatches("2026-10-22T15:30:00Z", "USD", filter)).toBe(false);
	expect(recordMatches("2026-10-22T16:30:00Z", undefined, filter)).toBe(false);
	expect(
		recordMatches("2026-10-22T16:30:00Z", undefined, {
			...filter,
			market: "all",
		}),
	).toBe(true);
});
