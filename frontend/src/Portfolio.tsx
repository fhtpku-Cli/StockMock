import { ArrowRight, Clock } from "@phosphor-icons/react";
import { useQuery } from "@tanstack/react-query";
import { ActiveOrdersSummary } from "./ActiveOrdersSummary";
import { Alerts } from "./Alerts";
import { AssetHistory } from "./AssetHistory";
import { api, errorMessage, money, time } from "./api";
import { Composition } from "./Composition";
import { Positions } from "./Positions";
import { SignedValue } from "./SignedValue";
import { portfolioSchema } from "./schema";
import { Badge, ErrorNotice, Loading, Panel } from "./ui";
import type { SymbolError } from "./useInstrumentNavigation";
export function PortfolioView({
	onExplore,
	onCash,
	onSymbol,
	onOrders,
	symbolError = null,
	onReviewOpen,
}: {
	readonly onExplore: () => void;
	readonly onCash: () => void;
	readonly onSymbol: (symbol: string) => void;
	readonly onOrders: () => void;
	readonly symbolError?: SymbolError;
	readonly onReviewOpen?: () => void;
}) {
	const query = useQuery({
		queryKey: ["portfolio"],
		queryFn: ({ signal }) =>
			api("portfolio", portfolioSchema, undefined, { signal }),
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
						本轮收益 <SignedValue value={p.pnl_cny} text={money(p.pnl_cny)} />
					</span>
					<span className="muted">初始本金 {money(p.initial_capital)}</span>
				</div>
				<dl className="hero-cash">
					<div>
						<dt>人民币可用资金</dt>
						<dd>{money(cny?.available ?? null)}</dd>
					</div>
					<div>
						<dt>人民币委托占用</dt>
						<dd>{money(cny?.reserved ?? null)}</dd>
					</div>
				</dl>
			</section>
			<p className="footnote">
				估值状态：
				{p.valuation_status}
			</p>
			{p.warnings.map((w) => (
				<div className="notice" key={w}>
					{w}
				</div>
			))}
			<ActiveOrdersSummary value={p.active_orders} onOrders={onOrders} />
			<div className="stack">
				<Positions
					positions={p.positions}
					onSymbol={onSymbol}
					symbolError={symbolError}
					onExplore={onExplore}
				/>
				<Alerts
					onSymbol={onSymbol}
					symbolError={symbolError}
					{...(onReviewOpen && { onReviewOpen })}
					compact
				/>
				{p.composition && <Composition value={p.composition} />}
				<div className="dashboard-grid">
					<AssetHistory />
					<Panel
						title="现金账户"
						action={
							<button type="button" className="text-button" onClick={onCash}>
								明细与换汇 <ArrowRight />
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
							外币买入时可自动换汇，卖出所得留在对应币种账户。每笔成交收取 0.05%
							模拟费用。
						</p>
					</Panel>
				</div>
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
			</div>
		</>
	);
}
