import { useQuery } from "@tanstack/react-query";
import { Alerts } from "./Alerts";
import { api, errorMessage, money, price, time } from "./api";
import { ChangeLine } from "./ChangeLine";
import { canVerifyOnBuy, tradingLabel } from "./instrumentAdmission";
import { MarketSession } from "./MarketSession";
import { changeDisplay } from "./marketView";
import { Plans } from "./Plans";
import { PriceHistory } from "./PriceHistory";
import { SignedValue } from "./SignedValue";
import type { Instrument } from "./schema";
import { historySchema } from "./schema";
import { Badge, ErrorNotice, Loading, Modal } from "./ui";
import { useOnline } from "./useOnline";
import { useTradeReference } from "./useTradeReference";
import { useWatchlist } from "./useWatchlist";
export function Detail({
	instrument,
	onClose,
	onTrade,
}: {
	readonly instrument: Instrument;
	readonly onClose: () => void;
	readonly onTrade: (side: "buy" | "sell") => void;
}) {
	const online = useOnline();
	const { toggle, contains, pending } = useWatchlist();
	const { quote, portfolio } = useTradeReference(instrument.symbol);
	const position = portfolio.data?.positions.find(
		(p) => p.symbol === instrument.symbol,
	);
	const history = useQuery({
		queryKey: ["history", instrument.symbol],
		queryFn: () =>
			api(
				`instruments/${encodeURIComponent(instrument.symbol)}/history`,
				historySchema,
			),
	});
	const verifyOnBuy = canVerifyOnBuy(instrument);
	const tickSize = quote.data?.tick_size ?? instrument.tick_size;
	const change =
		quote.data && quote.data.change_status !== "market_disabled"
			? changeDisplay(quote.data, instrument.market, new Date())
			: null;
	return (
		<Modal title={instrument.name} onClose={onClose} wide>
			<div className="detail-meta">
				<span>
					{instrument.symbol} · {instrument.market} · {instrument.currency}
				</span>
				<Badge tone={instrument.tradable ? "good" : "warn"}>
					{tradingLabel(instrument)}
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
						{price(
							quote.data.price,
							quote.data.currency,
							tickSize ?? undefined,
						)}
					</div>
					{change && (
						<ChangeLine
							value={change}
							currency={quote.data.currency}
							tickSize={tickSize ?? undefined}
						/>
					)}
					<p className="footnote">
						报价时间 {time(quote.data.market_time)}（北京时间） ·{" "}
						{quote.data.source}
					</p>
					<p className="notice">
						{quote.data.message || "行情可能存在延迟，请以报价时间判断时效。"}
					</p>
				</>
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
					disabled={
						!online ||
						(!instrument.tradable && !verifyOnBuy) ||
						(instrument.tradable && !quote.isSuccess)
					}
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
			) : (
				<PriceHistory
					key={instrument.symbol}
					symbol={instrument.symbol}
					name={instrument.name}
					market={instrument.market}
					currency={instrument.currency}
					tickSize={tickSize ?? null}
					rows={history.data}
				/>
			)}
			<MarketSession market={instrument.market} symbol={instrument.symbol} />
			<Plans symbol={instrument.symbol} currency={instrument.currency} />
			<Alerts symbol={instrument.symbol} />
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
					<dd>
						{instrument.tradable
							? "买卖各 0.05%"
							: verifyOnBuy
								? "核验通过后买卖各 0.05%"
								: "仅观察，暂不适用"}
					</dd>
				</div>
			</dl>
			{instrument.rule_source && (
				<p className="footnote instrument-rule-source">
					证券资料与规则来源：{instrument.rule_source} · 核对日期{" "}
					{instrument.verified_on ?? "未注明"}
				</p>
			)}
			{instrument.restriction &&
				(!quote.isSuccess || instrument.restriction !== quote.data.message) && (
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
						<dd>
							<SignedValue
								value={position.pnl}
								text={money(position.pnl, position.currency)}
							/>
						</dd>
					</div>
				</dl>
			)}
			{toggle.isError && <ErrorNotice message={errorMessage(toggle.error)} />}
		</Modal>
	);
}
