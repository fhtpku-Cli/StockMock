import { useId } from "react";
import type { HistoryRow } from "./schema";

export function VolumeHistory({
	rows,
	market,
	formatPrice,
	selected,
	onSelect,
}: {
	readonly rows: readonly HistoryRow[];
	readonly formatPrice: (value: string) => string;
	readonly selected: HistoryRow | undefined;
	readonly onSelect: (time: string) => void;
	readonly market: string;
}) {
	const dateId = useId();
	const source = market === "US" ? "新浪日线" : "腾讯日线";
	return (
		<section aria-label="日成交量">
			<div className="section-heading">
				<h3>逐日价格与成交量</h3>
			</div>
			{selected ? (
				<>
					<div className="field">
						<label htmlFor={dateId}>市场日期（{market} 当地）</label>
						<select
							id={dateId}
							value={selected.time}
							onChange={(event) => onSelect(event.target.value)}
						>
							{[...rows].reverse().map((row) => (
								<option key={row.time} value={row.time}>
									{row.time.slice(0, 10)}
								</option>
							))}
						</select>
					</div>
					<dl className="summary-list" aria-live="polite">
						<div>
							<dt>
								{selected.complete === true
									? "收盘价"
									: "日线价格（收盘未确认）"}
							</dt>
							<dd>{formatPrice(selected.close)}</dd>
						</div>
						<div>
							<dt>成交量（来源单位）</dt>
							<dd>
								{String(selected.volume)
									.split(".")
									.map((part, index) =>
										index === 0
											? part.replace(/\B(?=(\d{3})+(?!\d))/g, ",")
											: part,
									)
									.join(".")}
							</dd>
						</div>
					</dl>
					<p className="footnote">
						{source} · {selected.time.slice(0, 10)}
						（市场当地日期）。保留来源单位，未换算为股或份；可对照同一标的各日数据，不同市场或来源不可直接比较。
					</p>
				</>
			) : (
				<p className="chart-empty">暂无日成交量数据。</p>
			)}
		</section>
	);
}
