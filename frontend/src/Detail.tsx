import { useQuery } from "@tanstack/react-query";
import { Alerts } from "./Alerts";
import { api, errorMessage, money, price, time } from "./api";
import { MarketSession } from "./MarketSession";
import { Plans } from "./Plans";
import type { Instrument } from "./schema";
import { historySchema, portfolioSchema, quoteSchema } from "./schema";
import { Badge, ErrorNotice, Loading, Modal } from "./ui";
import { useEnableInstrument } from "./useEnableInstrument";
import { useOnline } from "./useOnline";
import { useWatchlist } from "./useWatchlist";
import { VolumeHistory } from "./VolumeHistory";
export function Detail({
	instrument,
	onClose,
	onTrade,
	onEnabled,
}: {
	readonly instrument: Instrument;
	readonly onClose: () => void;
	readonly onTrade: (side: "buy" | "sell") => void;
	readonly onEnabled: (instrument: Instrument) => void;
}) {
	const online = useOnline();
	const enable = useEnableInstrument(onEnabled);
	const canEnable =
		!instrument.tradable && ["CN", "US"].includes(instrument.market);
	const { toggle, contains, pending } = useWatchlist();
	const portfolio = useQuery({
		queryKey: ["portfolio"],
		queryFn: () => api("portfolio", portfolioSchema),
	});
	const position = portfolio.data?.positions.find(
		(p) => p.symbol === instrument.symbol,
	);
	const quote = useQuery({
		queryKey: ["quote", instrument.symbol],
		queryFn: () =>
			api(
				`instruments/${encodeURIComponent(instrument.symbol)}/quote`,
				quoteSchema,
			),
	});
	const history = useQuery({
		queryKey: ["history", instrument.symbol],
		queryFn: () =>
			api(
				`instruments/${encodeURIComponent(instrument.symbol)}/history`,
				historySchema,
			),
	});
	const tickSize = quote.data?.tick_size ?? instrument.tick_size;
	const prices = history.data?.map((p) => Number(p.close)) ?? [];
	const low = Math.min(...prices);
	const high = Math.max(...prices);
	const range = high - low || 1;
	const points = prices
		.map(
			(p, i) =>
				`${20 + (i / Math.max(prices.length - 1, 1)) * 660},${160 - ((p - low) / range) * 130}`,
		)
		.join(" ");
	return (
		<Modal title={instrument.name} onClose={onClose} wide>
			<div className="detail-meta">
				<span>
					{instrument.symbol} · {instrument.market} · {instrument.currency}
				</span>
				<Badge tone={instrument.tradable ? "good" : "warn"}>
					{instrument.tradable ? "可模拟交易" : "仅观察"}
				</Badge>
			</div>
			<MarketSession market={instrument.market} symbol={instrument.symbol} />
			{quote.isPending ? (
				<Loading />
			) : quote.isError ? (
				<ErrorNotice
					message={errorMessage(quote.error)}
					retry={() => void quote.refetch()}
				/>
			) : (
				<>
					<div className="quote-price">
						{price(
							quote.data.price,
							quote.data.currency,
							tickSize ?? undefined,
						)}
					</div>
					<p className="footnote">
						报价时间 {time(quote.data.market_time)}（北京时间） ·{" "}
						{quote.data.source}
					</p>
					<p className="notice">
						{quote.data.message || "行情可能存在延迟，请以报价时间判断时效。"}
					</p>
				</>
			)}
			<Plans symbol={instrument.symbol} currency={instrument.currency} />
			<Alerts symbol={instrument.symbol} />
			<div className="section-heading">
				<h3>历史收盘价</h3>
				<span className="muted">日线</span>
			</div>
			{history.isPending ? (
				<Loading />
			) : history.isError ? (
				<ErrorNotice
					message="历史价格与成交量暂不可用。"
					retry={() => void history.refetch()}
				/>
			) : prices.length < 2 ? (
				<p className="chart-empty">历史样本不足，暂不绘制走势。</p>
			) : (
				<figure className="price-chart">
					<svg
						viewBox="0 0 700 190"
						role="img"
						aria-label={`${instrument.name}近 ${prices.length} 个样本收盘价，最低 ${low}，最高 ${high} ${instrument.currency}`}
					>
						<path d="M20 30H680 M20 95H680 M20 160H680" className="grid-line" />
						<polyline
							points={points}
							fill="none"
							stroke="currentColor"
							strokeWidth="2.5"
							vectorEffect="non-scaling-stroke"
						/>
					</svg>
					<figcaption>
						<span>{history.data?.[0]?.time.slice(0, 10)}</span>
						<span>{history.data?.at(-1)?.time.slice(0, 10)}</span>
					</figcaption>
				</figure>
			)}
			{history.isSuccess && (
				<VolumeHistory
					key={instrument.symbol}
					rows={history.data}
					market={instrument.market}
				/>
			)}
			<dl className="summary-list">
				{instrument.min_buy_quantity != null &&
					instrument.quantity_step != null && (
						<div>
							<dt>买入数量规则</dt>
							<dd>
								{instrument.min_buy_quantity} 股 / 份起，每次递增{" "}
								{instrument.quantity_step} 股 / 份
							</dd>
						</div>
					)}
				<div>
					<dt>每手数量</dt>
					<dd>
						{instrument.lot_size === null
							? "待核验"
							: `${instrument.lot_size} 股 / 份`}
					</dd>
				</div>
				<div>
					<dt>最小价位</dt>
					<dd>
						{tickSize === null
							? "待核验"
							: `${tickSize} ${instrument.currency}`}
						{instrument.tick_rule === "HK" ? "（随价格区间变化）" : ""}
					</dd>
				</div>
				<div>
					<dt>模拟费用</dt>
					<dd>{instrument.tradable ? "买卖各 0.05%" : "仅观察，暂不适用"}</dd>
				</div>
			</dl>
			{instrument.rule_source && (
				<p className="footnote instrument-rule-source">
					证券资料与规则来源：{instrument.rule_source} · 核对日期{" "}
					{instrument.verified_on ?? "未注明"}
				</p>
			)}
			{instrument.restriction && (
				<p className="notice">{instrument.restriction}</p>
			)}
			{position && (
				<dl className="summary-list">
					<div>
						<dt>当前持仓 / 可卖</dt>
						<dd>
							{position.quantity} / {position.sellable}
						</dd>
					</div>
					<div>
						<dt>每股成本（含费用）</dt>
						<dd>{price(position.average_cost, position.currency)}</dd>
					</div>
					<div>
						<dt>参考浮动收益</dt>
						<dd>{money(position.pnl, position.currency)}</dd>
					</div>
				</dl>
			)}
			{toggle.isError && <ErrorNotice message={errorMessage(toggle.error)} />}
			{canEnable && (
				<div className="notice" aria-live="polite" aria-busy={enable.isPending}>
					<p>
						通过交易规则与行情核验后，可启用此证券的模拟交易；核验未通过时继续保留观察。
					</p>
					<button
						type="button"
						className="button primary"
						disabled={!online || enable.isPending}
						onClick={() => enable.mutate(instrument.symbol)}
					>
						{enable.isPending
							? "正在核验交易条件…"
							: enable.isError
								? "重试启用模拟交易"
								: "启用模拟交易"}
					</button>
					{enable.isError && (
						<ErrorNotice message={errorMessage(enable.error)} />
					)}
				</div>
			)}
			<div className="button-row discovery-detail-actions">
				<button
					type="button"
					className="button"
					disabled={pending || !online}
					onClick={() => toggle.mutate(instrument)}
				>
					{contains(instrument.symbol) ? "移除自选" : "加入自选"}
				</button>
				<button
					type="button"
					className="button primary"
					disabled={!online || !instrument.tradable || !quote.isSuccess}
					onClick={() => onTrade("buy")}
				>
					模拟买入
				</button>
				<button
					type="button"
					className="button"
					disabled={!online || !instrument.tradable || !quote.isSuccess}
					onClick={() => onTrade("sell")}
				>
					模拟卖出
				</button>
			</div>
			<p className="footnote">常规交易时段 · 当日有效 · 延迟成交与撤单</p>
		</Modal>
	);
}
