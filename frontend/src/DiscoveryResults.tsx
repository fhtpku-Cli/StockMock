import { Plus, Star } from "@phosphor-icons/react";
import { canVerifyOnBuy, tradingLabel } from "./instrumentAdmission";
import type { Instrument } from "./schema";
import { Badge } from "./ui";

import { useOnline } from "./useOnline";

export function DiscoveryResults({
	items,
	onSelect,
	onBuy,
	onToggle,
	contains,
	pending,
	sharedRules,
}: {
	readonly items: readonly Instrument[];
	readonly onSelect: (instrument: Instrument) => void;
	readonly onBuy: (instrument: Instrument) => void;
	readonly onToggle: (instrument: Instrument) => void;
	readonly contains: (symbol: string) => boolean;
	readonly pending: boolean;
	readonly sharedRules: ReadonlyMap<string, string>;
}) {
	const online = useOnline();
	return (
		<div className="security-list">
			{items.map((instrument) => (
				<div
					className="security-row"
					key={instrument.symbol}
					data-symbol={instrument.symbol}
				>
					<button
						type="button"
						className="security-main"
						onClick={() => onSelect(instrument)}
					>
						<span className="security-avatar">
							{instrument.name.slice(0, 1)}
						</span>
						<span className="security-copy">
							<strong>{instrument.name}</strong>
							<small>
								{instrument.symbol} · {instrument.market} ·{" "}
								{instrument.kind === "ETF" ? "ETF" : "股票"}
							</small>
							{instrument.restriction &&
								(!instrument.tradable ||
									sharedRules.get(instrument.market) !==
										instrument.restriction) && (
									<small>{instrument.restriction}</small>
								)}
						</span>
					</button>
					<Badge tone={instrument.tradable ? "good" : "warn"}>
						{tradingLabel(instrument)}
					</Badge>
					<button
						type="button"
						className="button small"
						disabled={
							!online || (!instrument.tradable && !canVerifyOnBuy(instrument))
						}
						onClick={() => onBuy(instrument)}
					>
						买入
					</button>
					<button
						type="button"
						className="icon-button"
						aria-label={`${contains(instrument.symbol) ? "移除" : "添加"}自选 ${instrument.name}`}
						disabled={pending || !online}
						onClick={() => onToggle(instrument)}
					>
						{contains(instrument.symbol) ? (
							<Star weight="fill" className="star" size={20} />
						) : (
							<Plus size={20} />
						)}
					</button>
				</div>
			))}
		</div>
	);
}
