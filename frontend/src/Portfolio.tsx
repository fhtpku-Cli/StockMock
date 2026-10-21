import { useQuery } from "@tanstack/react-query";
import {
	ArrowUpRight,
	Wallet,
	ArrowRight,
	Clock,
	LockSimple,
} from "@phosphor-icons/react";
import { api, errorMessage, money, price, number, time } from "./api";
import { portfolioSchema } from "./schema";
import { Badge, Empty, ErrorNotice, Loading, Panel } from "./ui";
export function PortfolioView({
	onExplore,
	onCash,
	onSymbol,
}: {
	readonly onExplore: () => void;
	readonly onCash: () => void;
	readonly onSymbol: (symbol: string) => void;
}) {
	const query = useQuery({
		queryKey: ["portfolio"],
		queryFn: () => api("portfolio", portfolioSchema),
		refetchInterval: 30000,
	});
	if (query.isPending) return <Loading />;
	if (query.isError)
		return (
			<ErrorNotice
				message={errorMessage(query.error)}
				retry={() => void query.refetch()}
			/>
		);
	const p = query.data;
	const cny = p.cash.find((c) => c.currency === "CNY");
	return (
		<>
			<section className="asset-hero">
				<div className="hero-top">
					<span className="eyebrow">
						总资产 <span>· 折合人民币</span>
					</span>
					<Badge tone="good">个人模拟账户</Badge>
				</div>
				<div className="asset-value">{money(p.total_assets_cny)}</div>
				<div className="hero-bottom">
					<span>
						本轮收益{" "}
						<strong
							className={
								p.pnl_cny && Number(p.pnl_cny) < 0 ? "negative" : "positive"
							}
						>
							{p.pnl_cny && Number(p.pnl_cny) > 0 ? "+" : ""}
							{money(p.pnl_cny)}
						</strong>
					</span>
					<span className="muted">初始本金 {money(p.initial_capital)}</span>
				</div>
			</section>
			{p.warnings.map((w) => (
				<div className="notice" key={w}>
					{w}
				</div>
			))}
			<div className="metric-grid">
				<div className="metric">
					<span>
						<Wallet />
						人民币可用资金
					</span>
					<strong>{money(cny?.available ?? null)}</strong>
					<small>可用于买入与自动换汇</small>
				</div>
				<div className="metric">
					<span>
						<LockSimple />
						人民币委托占用
					</span>
					<strong>{money(cny?.reserved ?? null)}</strong>
					<small>撤单确认后才会释放</small>
				</div>
				<div className="metric">
					<span>
						<ArrowUpRight />
						持仓标的
					</span>
					<strong>
						{p.positions.length}
						<em> 只</em>
					</strong>
					<small>从每一次有理由的决策开始</small>
				</div>
			</div>
			<div className="dashboard-grid">
				<div className="stack">
					<Panel
						title="我的持仓"
						action={
							<button type="button" className="text-button" onClick={onExplore}>
								发现标的 <ArrowRight />
							</button>
						}
					>
						{p.positions.length === 0 ? (
							<Empty
								title="第一笔投资，从观察开始"
								action={
									<button
										type="button"
										className="button primary"
										onClick={onExplore}
									>
										去看看市场 <ArrowUpRight />
									</button>
								}
							>
								资金已就位。选择你熟悉的标的，写下判断，再开始练习。
							</Empty>
						) : (
							<div className="table-scroll">
								<table>
									<thead>
										<tr>
											<th>标的</th>
											<th>持仓 / 可卖</th>
											<th>市值</th>
											<th>浮动收益</th>
										</tr>
									</thead>
									<tbody>
										{p.positions.map((position) => (
											<tr key={position.symbol}>
												<td>
													<button
														type="button"
														className="symbol-link"
														onClick={() => onSymbol(position.symbol)}
													>
														{position.name}
														<small>{position.symbol}</small>
													</button>
												</td>
												<td>
													{number(position.quantity)}
													<small>可卖 {number(position.sellable)}</small>
												</td>
												<td>
													{money(position.market_value, position.currency)}
													<small>
														成本{" "}
														{price(position.average_cost, position.currency)}
													</small>
												</td>
												<td>
													{money(position.pnl, position.currency)}
													<small>{position.valuation_status}</small>
												</td>
											</tr>
										))}
									</tbody>
								</table>
							</div>
						)}
					</Panel>
					<Panel title="资产轨迹">
						<div className="chart-empty">
							<div className="chart-rule" />
							<span>每一轮练习，都值得留下轨迹</span>
							<p>
								当前尚无可展示的资产历史快照。成交与资金记录可在复盘中查看。
							</p>
						</div>
					</Panel>
				</div>
				<div className="stack">
					<Panel
						title="现金账户"
						action={
							<button type="button" className="text-button" onClick={onCash}>
								管理 <ArrowRight />
							</button>
						}
					>
						<div className="cash-list">
							{p.cash.map((c) => (
								<div className="cash-row" key={c.currency}>
									<span className="currency-icon">
										{c.currency === "CNY"
											? "¥"
											: c.currency === "USD"
												? "$"
												: "HK"}
									</span>
									<div>
										<strong>
											{
												({ CNY: "人民币", USD: "美元", HKD: "港币" } as const)[
													c.currency === "USD"
														? "USD"
														: c.currency === "HKD"
															? "HKD"
															: "CNY"
												]
											}
										</strong>
										<small>{c.currency} · 可用</small>
									</div>
									<b>{money(c.available, c.currency)}</b>
								</div>
							))}
						</div>
						<p className="footnote">
							买入港美股时自动补足外币；卖出后的外币留在对应账户。
						</p>
					</Panel>
					<section className="practice-note">
						<span className="eyebrow">保持一点耐心</span>
						<h2>
							先记录判断，
							<br />
							再观察结果。
						</h2>
						<p>
							价格会波动，决策可以复盘。每笔买卖按成交金额收取 0.05% 模拟费用。
						</p>
						<div className="worker">
							<Clock size={16} />
							<span>
								{p.worker.status === "running"
									? "委托处理正常"
									: p.worker.status === "starting"
										? "委托处理准备中"
										: p.worker.status}{" "}
								· {time(p.worker.last_run)}
							</span>
						</div>
					</section>
				</div>
			</div>
		</>
	);
}
