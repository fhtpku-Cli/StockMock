import { useQuery } from "@tanstack/react-query";
import { api, errorMessage, price, time } from "./api";
import { historySchema, quoteSchema } from "./schema";
import type { Instrument } from "./schema";
import { Badge, ErrorNotice, Loading, Modal } from "./ui";
export function Detail({
	instrument,
	onClose,
	onTrade,
}: {
	readonly instrument: Instrument;
	readonly onClose: () => void;
	readonly onTrade: (side: "buy" | "sell") => void;
}) {
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
						{price(quote.data.price, quote.data.currency, instrument.tick_size)}
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
			<div className="section-heading">
				<h3>历史收盘价</h3>
				<span className="muted">日线</span>
			</div>
			{history.isPending ? (
				<Loading />
			) : history.isError ? (
				<ErrorNotice
					message="历史行情暂不可用。"
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
			<dl className="summary-list">
				<div>
					<dt>每手数量</dt>
					<dd>{instrument.lot_size} 股 / 份</dd>
				</div>
				<div>
					<dt>最小价位</dt>
					<dd>
						{instrument.tick_size} {instrument.currency}
					</dd>
				</div>
				<div>
					<dt>模拟费用</dt>
					<dd>买卖各 0.05%</dd>
				</div>
			</dl>
			{instrument.restriction && (
				<p className="notice">{instrument.restriction}</p>
			)}
			<div className="button-row">
				<button
					type="button"
					className="button primary"
					disabled={!instrument.tradable || !quote.isSuccess}
					onClick={() => onTrade("buy")}
				>
					模拟买入
				</button>
				<button
					type="button"
					className="button"
					disabled={!instrument.tradable || !quote.isSuccess}
					onClick={() => onTrade("sell")}
				>
					模拟卖出
				</button>
			</div>
			<p className="footnote">常规交易时段 · 当日有效 · 延迟成交与撤单</p>
		</Modal>
	);
}
