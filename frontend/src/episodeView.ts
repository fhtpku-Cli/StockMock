import { money, number, price, time } from "./api";
import type { Episode, EpisodeEvent, EpisodesData } from "./schema";

// 查询键以 "orders" 开头：下单、撤单、核对委托成功后对 ["orders"] 的失效会一并重取本接口。
export const episodesKey = (symbol: string) =>
	["orders", "episodes", symbol] as const;

export const eventKindLabels = {
	first_buy: "首买",
	add: "加仓",
	reduce: "减仓",
	close: "清仓",
	split: "拆合股",
	dividend: "股息",
	anomaly: "异常成交",
} as const;

const paymentLabels = {
	paid: "已到账",
	receivable: "应收",
} as const;

export const OUTSIDE_EVENTS_LABEL = "段外事件";

export const RECONCILE_MISMATCH =
	"与当前持仓核对不上，以下分段仅供参考，请以委托记录为准。";

/** 市场当地日期 "2026-11-03" 显示为 "11/03"。 */
export const marketDay = (day: string): string =>
	`${day.slice(5, 7)}/${day.slice(8, 10)}`;

const beijingDay = (at: string): string =>
	new Intl.DateTimeFormat("sv-SE", { timeZone: "Asia/Shanghai" }).format(
		new Date(at),
	);

/** 事件时间以北京时间为主；市场日与北京日期不是同一天时补充市场当地日期。 */
export function eventTimeText(event: EpisodeEvent): string {
	// 拆合股与股息只记录生效日期，按日级约定排序，不显示推算出的具体时刻。
	if (event.kind === "split" || event.kind === "dividend")
		return `${marketDay(event.market_date)} 生效（市场当地日期）`;
	const base = `${time(event.at)}（北京时间）`;
	return event.market_date && event.market_date !== beijingDay(event.at)
		? `${base} · 市场日 ${marketDay(event.market_date)}`
		: base;
}

/** 持有天数：进行中为“已持有”，已清仓按自然日，同日买卖记“当日清仓”。 */
export function holdingText(episode: Episode): string {
	if (episode.status === "open") return `已持有 ${episode.holding_days} 天`;
	return episode.holding_days === 0
		? "当日清仓"
		: `持有 ${episode.holding_days} 天`;
}

/** 折叠行的一行摘要：段序号、状态、首买与清仓市场日、天数、买卖次数；边界未知时附文字说明。 */
export function episodeSummary(episode: Episode): string {
	const head = `第 ${episode.index} 段 · ${
		episode.status === "open" ? "进行中" : "已清仓"
	} · ${marketDay(episode.opened_market_date)} 首买`;
	const tail = `买 ${episode.buy_count} 次 卖 ${episode.sell_count} 次`;
	let middle: string;
	if (episode.status === "open") {
		middle = holdingText(episode);
	} else if (episode.holding_days === 0) {
		middle = "当日清仓";
	} else {
		const closed =
			episode.closed_market_date === null
				? "清仓"
				: `${marketDay(episode.closed_market_date)} 清仓`;
		middle = `${closed} · ${holdingText(episode)}`;
	}
	const note =
		episode.boundary_note === null ? "" : ` · ${episode.boundary_note}`;
	return `${head} · ${middle} · ${tail}${note}`;
}

/** 时间线主行：成交显示数量×价格，拆合股显示比例，股息显示每股金额，异常显示说明。 */
export function eventMainText(event: EpisodeEvent): string {
	switch (event.kind) {
		case "first_buy":
		case "add":
			return `买入 ${number(event.quantity ?? 0)} 股/份 × ${price(
				event.price,
				event.currency,
			)}`;
		case "reduce":
		case "close":
			return `卖出 ${number(event.quantity ?? 0)} 股/份 × ${price(
				event.price,
				event.currency,
			)}`;
		case "split":
			return `比例 ${event.ratio ?? "—"}`;
		case "dividend":
			return `每股股息 ${price(event.cash_per_share, event.currency)}`;
		case "anomaly":
			return event.note ?? "成交记录缺少必要字段，未参与持仓计算";
	}
}

/** 时间线次行：成交显示费用，股息显示合计金额与到账状态，各类都给出事件后持仓。 */
export function eventDetailTexts(event: EpisodeEvent): readonly string[] {
	const details: string[] = [];
	if (event.kind === "dividend") {
		if (event.cash_amount !== null) {
			const payment =
				event.payment_status === null
					? ""
					: `（${paymentLabels[event.payment_status]}）`;
			details.push(
				`股息 ${money(event.cash_amount, event.currency)}${payment}`,
			);
		}
	} else if (
		event.kind !== "anomaly" &&
		event.kind !== "split" &&
		event.fee !== null
	) {
		details.push(`费用 ${money(event.fee, event.currency)}`);
	}
	if (event.position_after !== null)
		details.push(`事件后持仓 ${number(event.position_after)} 股/份`);
	return details;
}

/** 段内最大持仓的数值（标签由调用处给出）；段内发生过拆合股时标注口径。 */
export function peakText(episode: Episode): string {
	const base = `${number(episode.peak_quantity)} 股/份`;
	return episode.events.some((event) => event.kind === "split")
		? `${base}（按拆合股后口径）`
		: base;
}

/** 重放结果与当前持仓不一致时的顶部文字说明；一致时返回 null。 */
export function reconcileText(data: EpisodesData): string | null {
	if (data.reconciled) return null;
	return data.reconcile_note ?? RECONCILE_MISMATCH;
}
