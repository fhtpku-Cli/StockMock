import { useId, useState } from "react";
import type { z } from "zod";
import type { historySchema } from "./schema";

export function VolumeHistory({
	rows,
	market,
}: {
	readonly rows: readonly z.infer<typeof historySchema>[number][];
	readonly market: string;
}) {
	const dateId = useId();
	const [selectedDate, setSelectedDate] = useState("");
	const days = [...rows].sort((a, b) => a.time.localeCompare(b.time));
	const selected = days.find((row) => row.time === selectedDate) ?? days.at(-1);
	const source = market === "US" ? "新浪日线" : "腾讯日线";
	return (
		<section aria-label="日成交量">
			<div className="section-heading">
				<h3>日成交量</h3>
				<span className="muted">来源单位</span>
			</div>
			{selected ? (
				<>
					<div className="field">
						<label htmlFor={dateId}>市场日期（{market} 当地）</label>
						<select
							id={dateId}
							value={selected.time}
							onChange={(event) => setSelectedDate(event.target.value)}
						>
							{[...days].reverse().map((row) => (
								<option key={row.time} value={row.time}>
									{row.time.slice(0, 10)}
								</option>
							))}
						</select>
					</div>
					<dl className="summary-list" aria-live="polite">
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
