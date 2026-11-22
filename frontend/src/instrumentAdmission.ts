import type { Instrument } from "./schema";

export function canVerifyOnBuy(item: Instrument) {
	return (
		!item.tradable &&
		item.lot_size === null &&
		(item.market === "CN" || item.market === "US")
	);
}

export function tradingLabel(item: Instrument) {
	if (item.tradable) return "可模拟交易";
	return canVerifyOnBuy(item) ? "买入时核验" : "仅观察";
}
