import { useQuery, useQueryClient } from "@tanstack/react-query";
import { marketSessionQuery, refreshMarketSession } from "./marketSessionQuery";
import type { MarketSession as Session } from "./marketSessionSchema";
import { useOnline } from "./useOnline";

const phaseLabels = {
	supported: "支持时段内（按日历）",
	before_open: "尚未进入今日支持时段",
	break: "午间休息（按日历）",
	unsupported_closing: "收盘阶段，本应用不支持",
	after_hours: "今日支持时段已结束",
	non_session_day: "非交易日（按日历）",
	unknown: "市场时段暂不可用",
} as const;
const dateFormat = new Intl.DateTimeFormat("sv-SE", {
	timeZone: "Asia/Shanghai",
	year: "numeric",
	month: "2-digit",
	day: "2-digit",
	hour: "2-digit",
	minute: "2-digit",
	second: "2-digit",
	hourCycle: "h23",
});

export function MarketSessionFacts({ value }: { readonly value: Session }) {
	return (
		<>
			<p>
				<strong>{phaseLabels[value.phase]}</strong>
			</p>
			<p className="footnote">
				查询时间 {dateFormat.format(new Date(value.as_of))}（北京时间）
			</p>
			{value.current_period && (
				<p>
					本段结束 {dateFormat.format(new Date(value.current_period.end))}
					（北京时间）
				</p>
			)}
			{value.phase !== "unknown" && (
				<p>
					{value.next_period
						? `下一支持时段 ${dateFormat.format(new Date(value.next_period.start))} 至 ${dateFormat.format(new Date(value.next_period.end))}（北京时间）`
						: "下一支持时段暂不可用"}
				</p>
			)}
			{value.phase === "unsupported_closing" && (
				<p>限价预挂仍须等待参考日实际收盘，并取得合格日线。</p>
			)}
		</>
	);
}

export function MarketSession({
	market,
	symbol,
}: {
	readonly market: string;
	readonly symbol: string;
}) {
	const online = useOnline();
	const client = useQueryClient();
	const options = marketSessionQuery(market, symbol);
	const query = useQuery(options);
	const querying = query.isFetching || query.isPending;
	const value = online && !querying && query.isSuccess ? query.data : null;
	return (
		<section
			className="market-session"
			aria-label="查询时市场时段"
			aria-busy={online && querying}
		>
			<div className="market-session-heading">
				<h3>查询时市场时段</h3>
				<button
					type="button"
					className="button"
					disabled={!online}
					onClick={() => void refreshMarketSession(client, options.queryKey)}
				>
					刷新市场时段
				</button>
			</div>
			<div aria-live="polite">
				{!online ? (
					<p>市场时段暂不可用，请联网后重试。</p>
				) : query.isError ? (
					<p>市场时段暂不可用，请刷新重试。</p>
				) : querying ? (
					<p>正在查询市场时段…</p>
				) : (
					value && <MarketSessionFacts value={value} />
				)}
			</div>
			<p className="footnote">
				前台停留时约每30秒更新。仅按交易日历说明本应用支持时段，不代表实时停市或个股停牌状态。
				{market === "HK"
					? "港股仅观察，暂不支持模拟交易。"
					: "限价单可预挂；市价模拟仅在支持时段受理，仍需通过证券资格、行情、资金等检查。"}
			</p>
		</section>
	);
}
