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
import { ChangeLine } from "./ChangeLine";
import { changeDisplay } from "./marketView";
import { SellabilityDetails } from "./SellabilityDetails";
import { SignedValue } from "./SignedValue";
import { Empty, ErrorNotice, Panel } from "./ui";
import type { SymbolError } from "./useInstrumentNavigation";
export function Positions({
	positions,
	onSymbol,
	onExplore,
	symbolError = null,
}: {
	readonly positions: readonly Position[];
	readonly onSymbol: (symbol: string) => void;
	readonly onExplore: () => void;
	readonly symbolError?: SymbolError;
}) {
	const [market, setMarket] = useState("");
	const [currency, setCurrency] = useState("");
	const [sort, setSort] = useState<PositionSort>("market_value_cny");
	const now = new Date();
	const rows = sortPositions(
		positions.filter(
			(row) =>
				(!market || row.market === market) &&
				(!currency || row.currency === currency),
		),
		sort,
	);
	const showChangeColumn = rows.some(
		(row) => row.change_status !== "market_disabled",
	);
	return (
		<Panel
			title="我的持仓"
			className="positions-panel"
			action={
				<div className="position-actions">
					<small>{positions.length} 只</small>
					<button type="button" className="text-button" onClick={onExplore}>
						发现标的 <ArrowRight />
					</button>
				</div>
			}
		>
			{positions.length === 0 ? (
				<Empty
					title="暂无持仓"
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
					买入成交后，这里会显示持仓数量、市值和浮动收益。
				</Empty>
			) : (
				<>
					<details className="position-filters">
						<summary>
							筛选与排序 · {rows.length} / {positions.length} 只
						</summary>
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
									<option value="market_value_cny">
										人民币市值 · 从高到低
									</option>
									<option value="weight_percent">仓位比例 · 从高到低</option>
									<option value="pnl_percent">浮动收益率 · 从高到低</option>
								</select>
							</label>
						</div>
						<p className="footnote">排序时缺失值排在最后，同值按代码排列。</p>
					</details>
					{rows.length === 0 ? (
						<p className="chart-empty">没有符合筛选条件的持仓。</p>
					) : (
						<div className="table-scroll">
							<table className="positions-table">
								<thead>
									<tr>
										<th>标的 / 行情时间</th>
										{showChangeColumn && (
											<th className="change-column">今日涨跌（每股）</th>
										)}
										<th>持仓 / 可卖</th>
										<th>原币市值 / 成本</th>
										<th>人民币市值 / 占比</th>
										<th>原币浮动收益 / 收益率</th>
									</tr>
								</thead>
								<tbody>
									{rows.map((row) => {
										const change = changeDisplay(row, row.market, now);
										return (
											<tr key={row.symbol} data-position={row.symbol}>
												<td className="position-name">
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
													{symbolError?.symbol === row.symbol && (
														<ErrorNotice
															message={symbolError.message}
															retry={() => onSymbol(row.symbol)}
														/>
													)}
													{row.change_status !== "market_disabled" && (
														<span className="change-inline">
															<ChangeLine
																value={change}
																currency={row.currency}
																compact
															/>
														</span>
													)}
												</td>
												{showChangeColumn && (
													<td className="change-column">
														{row.change_status !== "market_disabled" && (
															<ChangeLine
																value={change}
																currency={row.currency}
																compact
															/>
														)}
													</td>
												)}
												<td className="position-quantity">
													<span className="position-label">持仓数量</span>
													{number(row.quantity)}
													<small>可卖 {number(row.sellable)}</small>
													<small>卖单占用 {number(row.reserved)}</small>
													<SellabilityDetails
														value={row.sellability}
														reserved={row.reserved}
													/>
												</td>
												<td className="position-cost">
													<span className="position-label">原币市值</span>
													{money(row.market_value, row.currency)}
													<small>
														单位成本 {price(row.average_cost, row.currency)}
													</small>
													<small>
														剩余成本 {money(row.cost, row.currency)}
													</small>
												</td>
												<td className="position-value">
													<span className="position-label">人民币市值</span>
													{money(row.market_value_cny)}
													<small>占总资产 {percent(row.weight_percent)}</small>
													{row.market_value_cny === null && (
														<small>人民币估值缺失，请查看上方估值说明</small>
													)}
												</td>
												<td className="position-pnl">
													<span className="position-label">原币浮动收益</span>
													<SignedValue
														value={row.pnl}
														text={money(row.pnl, row.currency)}
													/>
													<small>
														<SignedValue
															value={row.pnl_percent}
															text={percent(row.pnl_percent)}
														/>
													</small>
													{row.market_value === null && (
														<small>原币估值缺失，请查看上方说明</small>
													)}
												</td>
											</tr>
										);
									})}
								</tbody>
							</table>
						</div>
					)}
					<details className="reading-note">
						<summary>持仓参考估值与收益说明</summary>
						<p className="footnote">
							仓位按全账户总资产计算。浮动收益按剩余持仓成本（含买入费用）计算，未扣未来卖出费。原币盈亏分币种查看，公司行动需人工核对。今日涨跌为每股相对供应商昨收的变化。
						</p>
					</details>
				</>
			)}
		</Panel>
	);
}
