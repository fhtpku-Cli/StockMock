import { ArrowRight } from "@phosphor-icons/react";
import { useState } from "react";
import {
	marketName,
	type Position,
	type PositionSort,
	percent,
	sortPositions,
} from "./analysis";
import { money, number, price, time } from "./api";
import { SellabilityDetails } from "./SellabilityDetails";
import { Empty, Panel } from "./ui";
export function Positions({
	positions,
	onSymbol,
	onExplore,
}: {
	readonly positions: readonly Position[];
	readonly onSymbol: (symbol: string) => void;
	readonly onExplore: () => void;
}) {
	const [market, setMarket] = useState("");
	const [currency, setCurrency] = useState("");
	const [sort, setSort] = useState<PositionSort>("market_value_cny");
	const rows = sortPositions(
		positions.filter(
			(row) =>
				(!market || row.market === market) &&
				(!currency || row.currency === currency),
		),
		sort,
	);
	return (
		<Panel
			title="我的持仓"
			action={
				<button type="button" className="text-button" onClick={onExplore}>
					发现标的 <ArrowRight />
				</button>
			}
		>
			{positions.length === 0 ? (
				<Empty
					title="第一笔投资，从观察开始"
					action={
						<button
							type="button"
							className="button primary"
							onClick={onExplore}
						>
							去看看市场
						</button>
					}
				>
					资金已就位。选择你熟悉的标的，写下判断，再开始练习。
				</Empty>
			) : (
				<>
					<div className="analysis-filters">
						<label>
							持仓市场
							<select
								value={market}
								onChange={(event) => setMarket(event.target.value)}
							>
								<option value="">全部市场</option>
								{[...new Set(positions.map((row) => row.market))]
									.sort()
									.map((value) => (
										<option key={value} value={value}>
											{marketName(value)}
										</option>
									))}
							</select>
						</label>
						<label>
							持仓币种
							<select
								value={currency}
								onChange={(event) => setCurrency(event.target.value)}
							>
								<option value="">全部币种</option>
								{[...new Set(positions.map((row) => row.currency))]
									.sort()
									.map((value) => (
										<option key={value}>{value}</option>
									))}
							</select>
						</label>
						<label>
							持仓排序
							<select
								value={sort}
								onChange={(event) => {
									const value = event.target.value;
									if (
										value === "market_value_cny" ||
										value === "weight_percent" ||
										value === "pnl_percent"
									)
										setSort(value);
								}}
							>
								<option value="market_value_cny">人民币市值 · 从高到低</option>
								<option value="weight_percent">仓位比例 · 从高到低</option>
								<option value="pnl_percent">浮动收益率 · 从高到低</option>
							</select>
						</label>
					</div>
					<p className="footnote">
						显示 {rows.length} / {positions.length}{" "}
						只；占比以全账户总资产计算。缺失排在最后，同值按代码排序。
					</p>
					{rows.length === 0 ? (
						<p className="chart-empty">没有符合筛选条件的持仓。</p>
					) : (
						<div className="table-scroll">
							<table className="positions-table">
								<thead>
									<tr>
										<th>标的 / 行情时间</th>
										<th>持仓 / 可卖</th>
										<th>原币市值 / 成本</th>
										<th>人民币市值 / 占比</th>
										<th>原币浮动收益 / 收益率</th>
									</tr>
								</thead>
								<tbody>
									{rows.map((row) => (
										<tr key={row.symbol} data-position={row.symbol}>
											<td>
												<button
													type="button"
													className="symbol-link"
													onClick={() => onSymbol(row.symbol)}
												>
													{row.name}
													<small>
														{row.symbol} · {marketName(row.market)}
													</small>
												</button>
												<small>{time(row.price_time)}</small>
											</td>
											<td>
												{number(row.quantity)}
												<small>可卖 {number(row.sellable)}</small>
												<small>卖单占用 {number(row.reserved)}</small>
												<SellabilityDetails
													value={row.sellability}
													reserved={row.reserved}
												/>
											</td>
											<td>
												{money(row.market_value, row.currency)}
												<small>
													单位成本 {price(row.average_cost, row.currency)}
												</small>
												<small>剩余成本 {money(row.cost, row.currency)}</small>
											</td>
											<td>
												{money(row.market_value_cny)}
												<small>占总资产 {percent(row.weight_percent)}</small>
												{row.market_value_cny === null && (
													<small>人民币估值缺失，请查看上方估值说明</small>
												)}
											</td>
											<td>
												{money(row.pnl, row.currency)}
												<small>{percent(row.pnl_percent)}</small>
												<small>
													{row.market_value === null
														? "原币估值缺失，请查看上方说明"
														: row.market_value_cny === null
															? "原币可估；人民币估值缺失"
															: "参考估值；公司行动需核对"}
												</small>
											</td>
										</tr>
									))}
								</tbody>
							</table>
						</div>
					)}
					<div className="position-sellability-list">
						{rows
							.filter(
								(row) =>
									!row.sellability ||
									row.sellability.locked_quantity > 0 ||
									row.reserved > 0,
							)
							.map((row) => (
								<section key={row.symbol} aria-label={`${row.name}可卖说明`}>
									<strong>
										{row.name} · {row.symbol}
									</strong>
									<SellabilityDetails
										value={row.sellability}
										reserved={row.reserved}
									/>
								</section>
							))}
					</div>
					<p className="footnote">
						收益率以剩余持仓含买入费用成本计算，不计尚未发生的卖出费；不同币种的原币盈亏金额不相加。
					</p>
				</>
			)}
		</Panel>
	);
}
