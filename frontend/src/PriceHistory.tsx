import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { z } from "zod";
import { api, number, price, time } from "./api";
import {
	type ChartRange,
	chartGeometry,
	chartRanges,
	compareWithReference,
	defaultChartRange,
	rangeRows,
	referenceSymbol,
	shortDay,
	signedPercent,
	tradeMarks,
} from "./marketView";
import type { HistoryRow } from "./schema";
import { historySchema, orderSchema } from "./schema";
import { VolumeHistory } from "./VolumeHistory";

export function PriceHistory({
	symbol,
	name,
	market,
	currency,
	rows,
}: {
	readonly symbol: string;
	readonly name: string;
	readonly market: string;
	readonly currency: string;
	readonly rows: readonly HistoryRow[];
}) {
	const [range, setRange] = useState<ChartRange>(defaultChartRange);
	const visible = rangeRows(rows, range);
	const orders = useQuery({
		queryKey: ["orders"],
		queryFn: ({ signal }) =>
			api("orders", z.array(orderSchema), undefined, { signal }),
	});
	const marks = tradeMarks(orders.data ?? [], symbol, market, visible);
	const chart = chartGeometry(visible, marks);
	const last = visible.at(-1);
	const intraday = last?.complete === false;
	const indexOf = new Map(visible.map((row, index) => [row.time, index]));
	return (
		<>
			<fieldset className="segmented chart-range" aria-label="走势范围">
				{chartRanges.map((item) => (
					<button
						type="button"
						key={item.id}
						aria-pressed={range === item.id}
						onClick={() => setRange(item.id)}
					>
						{item.label}
					</button>
				))}
			</fieldset>
			{visible.length < 2 ? (
				<p className="chart-empty">历史样本不足，暂不绘制走势。</p>
			) : (
				<figure className="price-chart">
					<svg
						viewBox="0 0 700 190"
						role="img"
						aria-label={`${name}近 ${visible.length} 个交易日收盘价，最低 ${chart.low}，最高 ${chart.high} ${currency}`}
					>
						<path d="M20 30H680 M20 95H680 M20 160H680" className="grid-line" />
						<polyline
							points={chart.points}
							fill="none"
							stroke="currentColor"
							strokeWidth="2.5"
							vectorEffect="non-scaling-stroke"
						/>
						{marks
							.filter((mark) => mark.onChart)
							.map((mark) => {
								const x = chart.x(indexOf.get(mark.day) ?? 0);
								const y = chart.y(Number(mark.price));
								return (
									<path
										key={mark.id}
										data-trade-mark={mark.side}
										className={`trade-mark ${mark.side}`}
										d={
											mark.side === "buy"
												? `M${x} ${y - 7}L${x + 6} ${y + 4}L${x - 6} ${y + 4}Z`
												: `M${x} ${y + 7}L${x + 6} ${y - 4}L${x - 6} ${y - 4}Z`
										}
									/>
								);
							})}
					</svg>
					<figcaption>
						<span>{visible[0]?.time.slice(0, 10)}</span>
						<span>
							{last?.time.slice(0, 10)}
							{intraday &&
								`（${last?.retrieved_at ? `取得于北京时间 ${time(last.retrieved_at).slice(-5)}` : "取得时刻未知"}，是否已收盘未确认）`}
						</span>
					</figcaption>
				</figure>
			)}
			<p className="footnote">
				未复权价格：分红、送转的除权日可能出现跳空，不代表当天真实涨跌。
				{intraday &&
					"最后一根为取得时的价格，是否已收盘未确认（日线缓存最长 1 小时，可能比页头价格旧）。"}
				{last?.complete == null && "最后一根是否已收盘：未知。"}
			</p>
			<ReferenceLine
				symbol={symbol}
				market={market}
				rows={rows}
				range={range}
			/>
			{marks.length > 0 && (
				<section aria-label="我的成交" className="trade-list">
					<ul>
						{marks.map((mark) => (
							<li key={mark.id}>
								<span className={`trade-glyph ${mark.side}`} aria-hidden="true">
									{mark.side === "buy" ? "▲" : "▼"}
								</span>
								{shortDay(mark.day)} {mark.side === "buy" ? "买入" : "卖出"}{" "}
								{number(mark.quantity)} 股 @{price(mark.price, currency)}
								{!mark.onChart && (
									<small>
										成交日的日线暂缺；日线补齐后，刷新可在图上查看标记
									</small>
								)}
							</li>
						))}
					</ul>
					<p className="footnote">
						标记为实际成交价；日线为收盘价（最后一根为取得时的价格），两者不必重合。
					</p>
				</section>
			)}
			<VolumeHistory
				key={`${symbol}-${range}`}
				rows={visible}
				market={market}
			/>
		</>
	);
}

function ReferenceLine({
	symbol,
	market,
	rows,
	range,
}: {
	readonly symbol: string;
	readonly market: string;
	readonly rows: readonly HistoryRow[];
	readonly range: ChartRange;
}) {
	const reference = referenceSymbol(market, symbol);
	const history = useQuery({
		queryKey: ["history", reference?.symbol ?? ""],
		queryFn: () =>
			api(
				`instruments/${encodeURIComponent(reference?.symbol ?? "")}/history`,
				historySchema,
			),
		enabled: reference !== null,
	});
	if (reference === null) return null;
	const label = chartRanges.find((item) => item.id === range)?.label ?? "";
	const result = history.data
		? compareWithReference(rows, history.data, range)
		: null;
	return (
		<p className="reference-line" aria-live="polite">
			{history.isPending
				? "正在读取参照…"
				: result?.ok
					? `近${label}（${shortDay(result.start)}–${shortDay(result.end)}）：本证券 ${signedPercent(result.own)}｜${reference.name} ${signedPercent(result.reference)}`
					: "参照暂不可用"}
		</p>
	);
}
