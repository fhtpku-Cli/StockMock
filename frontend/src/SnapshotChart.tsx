import { money, time } from "./api";
import {
	type Drawdown,
	drawdownDayLabel,
	type Snapshot,
	snapshotSegments,
} from "./history";
export function SnapshotChart({
	items,
	drawdown,
}: {
	readonly items: readonly Snapshot[];
	/** 区间高点回落；旧后端不返回或未算出回落时不画特殊标记。 */
	readonly drawdown?: Drawdown | undefined;
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
	const marks =
		drawdown?.status === "computed" && drawdown.peak && drawdown.trough
			? { peak: drawdown.peak, trough: drawdown.trough }
			: null;
	const peakDay = marks?.peak.day ?? null;
	const troughDay = marks?.trough.day ?? null;

	return (
		<>
			{" "}
			{valid.length > 0 && (
				<figure className="price-chart">
					<svg
						viewBox="0 0 700 190"
						role="img"
						aria-label={`每日资产轨迹，缺失日期断开${
							marks
								? `；回落高点 ${drawdownDayLabel(marks.peak.day, marks.trough.day)} ${money(marks.peak.total_assets_cny)}，回落低点 ${drawdownDayLabel(marks.trough.day, marks.peak.day)} ${money(marks.trough.total_assets_cny)}`
								: ""
						}`}
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
								{segment.map((r) => {
									const cx = x(r.day),
										cy = y(r.total_assets_cny);
									// 高点用空心上三角、低点用实心下三角，与普通圆点区分。
									if (r.day === peakDay || r.day === troughDay) {
										const peak = r.day === peakDay;
										const point = peak ? marks?.peak : marks?.trough;
										const other = peak ? troughDay : peakDay;
										return (
											<path
												key={r.id}
												data-drawdown-mark={peak ? "peak" : "trough"}
												className={
													peak ? "chart-marker-peak" : "chart-marker-trough"
												}
												d={
													peak
														? `M${cx} ${cy - 6}L${cx + 6} ${cy + 4}L${cx - 6} ${cy + 4}Z`
														: `M${cx} ${cy + 6}L${cx + 6} ${cy - 4}L${cx - 6} ${cy - 4}Z`
												}
											>
												<title>
													{peak ? "回落高点 " : "回落低点 "}
													{drawdownDayLabel(r.day, other)}{" "}
													{point ? money(point.total_assets_cny) : ""}
												</title>
											</path>
										);
									}
									return (
										<circle
											key={r.id}
											cx={cx}
											cy={cy}
											r="3"
											fill="currentColor"
										>
											<title>
												{r.day} {money(r.total_assets_cny)}
											</title>
										</circle>
									);
								})}
							</g>
						))}
					</svg>
					<figcaption>
						<span>{rows[0]?.day}</span>
						{marks && (
							<span className="chart-legend">
								<span>△ 回落高点</span>
								<span>▼ 回落低点</span>
							</span>
						)}
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
										<small>
											{r.status === "complete"
												? "当天完整观察时间"
												: "未完整观察时间"}
											：{time(r.captured_at)}
										</small>
										{r.first_failure && (
											<small>
												首次未完整：{time(r.first_failure.captured_at)} ·{" "}
												{r.first_failure.status === "missing" ? "缺失" : "过时"}{" "}
												· {r.first_failure.issues.join("；")}
											</small>
										)}
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
