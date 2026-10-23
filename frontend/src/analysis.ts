import type { Portfolio } from "./schema";
export type Position = Portfolio["positions"][number];
export type PositionSort =
	| "market_value_cny"
	| "weight_percent"
	| "pnl_percent";
export function compareDecimal(a: string, b: string): number {
	const scale = Math.max(
		a.split(".")[1]?.length ?? 0,
		b.split(".")[1]?.length ?? 0,
	);
	const integer = (value: string) => {
		const [whole = "0", fraction = ""] = value.split(".");
		return BigInt(`${whole}${fraction.padEnd(scale, "0")}`);
	};
	const left = integer(a),
		right = integer(b);
	return left < right ? -1 : left > right ? 1 : 0;
}
export function sortPositions(
	rows: readonly Position[],
	key: PositionSort,
): Position[] {
	return [...rows].sort((a, b) => {
		const left = a[key],
			right = b[key];
		if (left === null && right !== null) return 1;
		if (right === null && left !== null) return -1;
		const rank =
			left === null || right === null ? 0 : compareDecimal(right, left);
		return rank || a.symbol.localeCompare(b.symbol, "en");
	});
}
export const percent = (value: string | null): string =>
	value === null ? "未知" : `${Number(value).toFixed(2)}%`;
export const marketName = (market: string): string =>
	({ CN: "A股", US: "美股", HK: "港股" })[market] ?? market;
export type DateRange = {
	readonly start_date: string;
	readonly end_date: string;
};
export function dateRangeError(range: DateRange): string | null {
	for (const value of [range.start_date, range.end_date]) {
		if (
			value &&
			(!/^\d{4}-\d{2}-\d{2}$/.test(value) ||
				Number.isNaN(Date.parse(value)) ||
				new Date(value).toISOString().slice(0, 10) !== value)
		)
			return "请输入有效日期。";
	}
	return range.start_date && range.end_date && range.start_date > range.end_date
		? "开始日期不能晚于结束日期。"
		: null;
}
export function filterParams(values: Readonly<Record<string, string>>): string {
	return new URLSearchParams(
		Object.entries(values).filter(([, value]) => value !== ""),
	).toString();
}
