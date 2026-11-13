import { marketTimezones } from "./marketSessionSchema";
import type { HistoryRow, Order, Quote } from "./schema";

export const chartRanges = [
	{ id: "1m", label: "1个月", days: 21 },
	{ id: "3m", label: "3个月", days: 63 },
	{ id: "1y", label: "1年", days: 252 },
] as const;
export type ChartRange = (typeof chartRanges)[number]["id"];
export const defaultChartRange: ChartRange = "3m";

export function rangeRows(
	rows: readonly HistoryRow[],
	range: ChartRange,
): HistoryRow[] {
	const days = chartRanges.find((item) => item.id === range)?.days ?? 63;
	return [...rows].sort((a, b) => a.time.localeCompare(b.time)).slice(-days);
}

type Change = Pick<
	Quote,
	| "change"
	| "change_percent"
	| "change_day"
	| "change_status"
	| "previous_close"
	| "change_base_day"
>;

const statusText = {
	pre_open: "开盘前不显示今日涨跌",
	no_previous_close: "昨收不可用",
	no_price: "不可用",
	quote_changed: "报价已更新，刷新后显示",
	market_disabled: "该市场今日涨跌暂不提供",
	unknown: "涨跌暂不可用",
} as const;

export function marketToday(market: string, now: Date): string | null {
	const zone = marketTimezones[market as keyof typeof marketTimezones];
	return zone
		? new Intl.DateTimeFormat("sv-SE", { timeZone: zone }).format(now)
		: null;
}

const monthDay = (day: string): string =>
	`${Number(day.slice(5, 7))}月${Number(day.slice(8, 10))}日`;

export type ChangeDisplay =
	| {
			readonly kind: "value";
			readonly label: string;
			readonly change: string;
			readonly percent: string;
			readonly direction: "up" | "down" | "flat";
			readonly base: string;
	  }
	| { readonly kind: "message"; readonly label: string; readonly text: string };

export function changeDisplay(
	value: Change,
	market: string,
	now: Date,
): ChangeDisplay {
	const today = marketToday(market, now);
	const label =
		value.change_day && value.change_day !== today
			? `${monthDay(value.change_day)} 涨跌`
			: "今日涨跌";
	const status = value.change_status;
	if (
		status === "available" &&
		value.change != null &&
		value.change_percent != null
	) {
		const sign = value.change.startsWith("-")
			? "down"
			: /^0(\.0+)?$/.test(value.change)
				? "flat"
				: "up";
		return {
			kind: "value",
			label,
			change: value.change,
			percent: `${sign === "up" ? "+" : ""}${Number(value.change_percent).toFixed(2)}%`,
			direction: sign,
			base: value.change_base_day
				? `相对供应商昨收（${monthDay(value.change_base_day)}）`
				: "相对供应商昨收",
		};
	}
	return {
		kind: "message",
		label,
		text: status && status !== "available" ? statusText[status] : "不可用",
	};
}

export function referenceSymbol(
	market: string,
	symbol: string,
): { readonly symbol: string; readonly name: string } | null {
	const reference =
		market === "CN"
			? { symbol: "510300.SS", name: "沪深300ETF" }
			: market === "US"
				? { symbol: "SPY", name: "SPY" }
				: null;
	return reference && reference.symbol !== symbol ? reference : null;
}

export type Comparison =
	| {
			readonly ok: true;
			readonly start: string;
			readonly end: string;
			readonly own: number;
			readonly reference: number;
	  }
	| { readonly ok: false };

/** 只用定稿日线的共有日期，不补点；共有日期少于 2 个时不可比较。 */
export function compareWithReference(
	own: readonly HistoryRow[],
	reference: readonly HistoryRow[],
	range: ChartRange,
): Comparison {
	const visible = rangeRows(own, range).filter((row) => row.complete === true);
	const references = new Map(
		reference
			.filter((row) => row.complete === true)
			.map((row) => [row.time, row.close]),
	);
	const common = visible.filter((row) => references.has(row.time));
	const first = common[0];
	const last = common.at(-1);
	if (!first || !last || common.length < 2) return { ok: false };
	const growth = (start: string, end: string) =>
		(Number(end) / Number(start) - 1) * 100;
	return {
		ok: true,
		start: first.time,
		end: last.time,
		own: growth(first.close, last.close),
		reference: growth(
			references.get(first.time) ?? first.close,
			references.get(last.time) ?? last.close,
		),
	};
}

export const signedPercent = (value: number): string =>
	`${value > 0 ? "+" : ""}${value.toFixed(1)}%`;
export const shortDay = monthDay;

export type TradeMark = {
	readonly id: string;
	readonly side: "buy" | "sell";
	readonly day: string;
	readonly price: string;
	readonly quantity: number;
	readonly onChart: boolean;
};

/** 成交日用 filled_at 换算市场当地日期；成交日尚无日线的成交只进入列表。 */
export function tradeMarks(
	orders: readonly Order[],
	symbol: string,
	market: string,
	visible: readonly HistoryRow[],
): TradeMark[] {
	const zone = marketTimezones[market as keyof typeof marketTimezones];
	const first = visible[0]?.time;
	if (!zone || !first) return [];
	const format = new Intl.DateTimeFormat("sv-SE", { timeZone: zone });
	const days = new Set(visible.map((row) => row.time));
	return orders
		.filter(
			(order) =>
				order.symbol === symbol &&
				order.status === "filled" &&
				order.filled_at !== null &&
				order.fill_price !== null,
		)
		.map((order) => ({
			id: order.id,
			side: order.side,
			day: format.format(new Date(order.filled_at ?? "")),
			price: order.fill_price ?? "",
			quantity: order.quantity,
			onChart: false,
		}))
		.filter((mark) => mark.day >= first)
		.map((mark) => ({ ...mark, onChart: days.has(mark.day) }))
		.sort((a, b) => a.day.localeCompare(b.day) || a.id.localeCompare(b.id));
}

export type ChartGeometry = {
	readonly points: string;
	readonly low: number;
	readonly high: number;
	readonly x: (index: number) => number;
	readonly y: (value: number) => number;
};

/** 纵轴同时容纳收盘价与可见成交价。 */
export function chartGeometry(
	rows: readonly HistoryRow[],
	marks: readonly TradeMark[],
): ChartGeometry {
	const values = [
		...rows.map((row) => Number(row.close)),
		...marks.filter((mark) => mark.onChart).map((mark) => Number(mark.price)),
	];
	const low = Math.min(...values);
	const high = Math.max(...values);
	const span = high - low || 1;
	const x = (index: number) =>
		20 + (index / Math.max(rows.length - 1, 1)) * 660;
	const y = (value: number) => 160 - ((value - low) / span) * 130;
	return {
		points: rows
			.map((row, index) => `${x(index)},${y(Number(row.close))}`)
			.join(" "),
		low,
		high,
		x,
		y,
	};
}
