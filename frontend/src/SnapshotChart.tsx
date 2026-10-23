import { money, time } from "./api";
import { type Snapshot, snapshotSegments } from "./history";
export function SnapshotChart({
	items,
}: {
	readonly items: readonly Snapshot[];
}) {
	const rows = [...items].sort((a, b) => a.day.localeCompare(b.day));
	const segments = snapshotSegments(rows);
	const valid = segments.flat();
	const amounts = valid.map((r) => Number(r.total_assets_cny));
	const low = Math.min(...amounts),
		high = Math.max(...amounts);
	const start = Date.parse(rows[0]?.day ?? "1970-01-01"),
		end = Date.parse(rows.at(-1)?.day ?? "1970-01-01");
	const x = (day: string) =>
		20 + ((Date.parse(day) - start) / Math.max(end - start, 86400000)) * 660;
	const y = (value: string | null) =>
		160 - ((Number(value) - low) / Math.max(high - low, 1)) * 130;

	return (
		<>
			{" "}
			{valid.length > 0 && (
				<figure className="price-chart">
					<svg
						viewBox="0 0 700 190"
						role="img"
						aria-label="每日资产轨迹，缺失日期断开"
					>
						<path d="M20 30H680 M20 95H680 M20 160H680" className="grid-line" />
						{segments.map((segment) => (
							<g key={segment[0]?.id}>
								<polyline
									data-testid="asset-segment"
									points={segment
										.map((r) => `${x(r.day)},${y(r.total_assets_cny)}`)
										.join(" ")}
									fill="none"
									stroke="currentColor"
									strokeWidth="2"
								/>
								{segment.map((r) => (
									<circle
										key={r.id}
										cx={x(r.day)}
										cy={y(r.total_assets_cny)}
										r="3"
										fill="currentColor"
									>
										<title>
											{r.day} {money(r.total_assets_cny)}
										</title>
									</circle>
								))}
							</g>
						))}
					</svg>
					<figcaption>
						<span>{rows[0]?.day}</span>
						<span>{rows.at(-1)?.day}</span>
					</figcaption>
				</figure>
			)}{" "}
			<details>
				<summary>查看每日金额与估值依据（{rows.length} 天）</summary>
				<div className="table-scroll">
					<table>
						<thead>
							<tr>
								<th>日期</th>
								<th>总资产 / 本轮收益</th>
								<th>估值状态</th>
							</tr>
						</thead>
						<tbody>
							{rows.map((r) => (
								<tr key={r.id}>
									<td>
										{r.day}
										<small>记录于 {time(r.captured_at)}</small>
									</td>
									<td>
										{money(r.total_assets_cny)}
										<small>{money(r.pnl_cny)}</small>
									</td>
									<td>
										{
											(
												{
													complete: "完整",
													stale: "过时，不连线",
													missing: "缺失，不连线",
												} as const
											)[r.status]
										}
										<small>{r.issues.join("；")}</small>
										{Object.entries(r.price_times).map(([symbol, t]) => (
											<small key={symbol}>
												{symbol} · {time(t)}
											</small>
										))}
									</td>
								</tr>
							))}
						</tbody>
					</table>
				</div>
			</details>
		</>
	);
}
