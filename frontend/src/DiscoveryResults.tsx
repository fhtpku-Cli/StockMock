import { Plus, Star } from "@phosphor-icons/react";
import type { Instrument } from "./schema";
import { Badge } from "./ui";

export function DiscoveryResults({
	items,
	onSelect,
	onToggle,
	contains,
	pending,
	sharedRules,
}: {
	readonly items: readonly Instrument[];
	readonly onSelect: (instrument: Instrument) => void;
	readonly onToggle: (instrument: Instrument) => void;
	readonly contains: (symbol: string) => boolean;
	readonly pending: boolean;
	readonly sharedRules: ReadonlyMap<string, string>;
}) {
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
						{instrument.tradable ? "可模拟交易" : "仅观察"}
					</Badge>
					<button
						type="button"
						className="icon-button"
						aria-label={`${contains(instrument.symbol) ? "移除" : "添加"}自选 ${instrument.name}`}
						disabled={pending}
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
