import { money } from "./api";
import type { RecentEnding } from "./schema";

// 查询键以 "orders" 开头：下单、撤单、核对委托成功后对 ["orders"] 的失效会一并重取本接口。
export const recentEndingsKey = (roundId: string) =>
	["orders", "recent-endings", roundId] as const;

const seenKey = (roundId: string) => `stockmock:recent-endings-seen:${roundId}`;

const instant = (value: string): number => {
	const parsed = Date.parse(value);
	return Number.isNaN(parsed) ? Number.NEGATIVE_INFINITY : parsed;
};

/** 读取本设备本轮已看到的最大结束时间；读取失败返回 undefined（不加「新」标记）。 */
export function readSeenEndedAt(roundId: string): string | null | undefined {
	try {
		return localStorage.getItem(seenKey(roundId));
	} catch {
		return undefined;
	}
}

/** 结束时间晚于已看到时间的条目为「新」；尚未看过时全部为新，读取失败时都不标。 */
export function newOrderIds(
	items: readonly RecentEnding[],
	seen: string | null | undefined,
): ReadonlySet<string> {
	if (seen === undefined) return new Set();
	const after = seen === null ? Number.NEGATIVE_INFINITY : instant(seen);
	return new Set(
		items.filter((i) => instant(i.ended_at) > after).map((i) => i.order_id),
	);
}

/** 只用接口返回的结束时间前移已看到位置，不使用设备时钟；写入失败时静默保留原状。 */
export function markEndingsSeen(
	roundId: string,
	items: readonly RecentEnding[],
): void {
	const latest = items.reduce<string | null>(
		(best, i) =>
			best === null || instant(i.ended_at) > instant(best) ? i.ended_at : best,
		null,
	);
	if (latest === null) return;
	try {
		const stored = localStorage.getItem(seenKey(roundId));
		if (stored !== null && instant(stored) >= instant(latest)) return;
		localStorage.setItem(seenKey(roundId), latest);
	} catch {
		// 本设备无法保存阅读位置时照常显示，只是下次仍可能标为新出现。
	}
}

export const currencyNames = {
	CNY: "人民币",
	USD: "美元",
	HKD: "港币",
} as const;

export const endingStatus = {
	filled: "已成交",
	cancelled: "已撤销",
	expired: "已过期",
	terminated: "已终止",
} as const;

/** 资金变动带明确正负号；颜色保持中性，资金流向不是涨跌。 */
export function signedMoney(amount: string, currency: string): string {
	const negative = amount.trim().startsWith("-");
	const text = money(negative ? amount.trim().slice(1) : amount, currency);
	return /[1-9]/.test(text) ? `${negative ? "−" : "+"}${text}` : text;
}

const nonZero = (value: string | null): value is string =>
	value !== null && /[1-9]/.test(value);

/** 每条委托的资金或持仓变化，按币种分行。 */
export function endingChanges(item: RecentEnding): readonly string[] {
	const lines = item.cash_changes
		.filter((c) => nonZero(c.amount))
		.map(
			(c) =>
				`${currencyNames[c.currency]} ${signedMoney(c.amount, c.currency)}`,
		);
	if (item.status !== "filled") {
		if (nonZero(item.released_cny))
			lines.push(`释放占用 人民币 ${money(item.released_cny, "CNY")}`);
		if (nonZero(item.released_foreign))
			lines.push(
				`释放占用 ${currencyNames[item.currency]} ${money(item.released_foreign, item.currency)}`,
			);
		if (item.released_shares)
			lines.push(`释放占用持仓 ${item.released_shares} 股 / 份`);
	}
	return lines;
}
