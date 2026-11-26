import { CaretDown, CaretUp } from "@phosphor-icons/react";
import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api, errorMessage, fullTime, price } from "./api";
import {
	endingChanges,
	endingStatus,
	markEndingsSeen,
	newOrderIds,
	readSeenEndedAt,
	recentEndingsKey,
} from "./recentEndingView";
import { recentEndingsSchema } from "./schema";
import { Badge, ErrorNotice, Panel } from "./ui";

export const RECENT_ENDINGS_MORE =
	"最近 3 天结束的委托多于 10 笔。委托记录按创建日期筛选，可在那里查看其余委托。";

/** 资产页「最近 3 天结束的委托」：只读当前轮次，失败不影响资产页其他部分。 */
export function RecentEndings({
	roundId,
	onLocate,
}: {
	readonly roundId: string;
	readonly onLocate: (orderId: string) => void;
}) {
	const query = useQuery({
		queryKey: recentEndingsKey(roundId),
		queryFn: ({ signal }) =>
			api("orders/recent-endings", recentEndingsSchema, undefined, { signal }),
		refetchInterval: 30000,
	});
	// 进入资产页时读取一次已看到位置；本次停留期间的「新」标记保持不变。
	const [seen] = useState(() => readSeenEndedAt(roundId));
	const [chosen, setChosen] = useState<boolean | null>(null);
	const items = query.data?.items ?? [];
	const open = chosen ?? items.length > 0;
	const fresh = newOrderIds(items, seen);
	useEffect(() => {
		if (open && query.data) markEndingsSeen(roundId, query.data.items);
	}, [open, query.data, roundId]);

	const title = query.isPending
		? "正在读取最近结束的委托"
		: !query.data
			? "最近结束的委托暂不可用"
			: items.length === 0
				? "最近 3 天没有结束的委托"
				: `最近 3 天结束的委托 ${items.length} 笔${fresh.size > 0 ? ` · 其中 ${fresh.size} 笔新出现` : ""}`;
	const toggle = query.data && (
		<button
			type="button"
			className="text-button"
			aria-expanded={open}
			aria-controls="recent-endings-body"
			onClick={() => setChosen(!open)}
		>
			{open ? "收起" : "展开"}
			{open ? <CaretUp /> : <CaretDown />}
		</button>
	);
	return (
		<Panel className="recent-endings" title={title} action={toggle}>
			{query.isError && (
				<ErrorNotice
					message={
						query.data
							? `最近结束的委托暂不可用，当前显示最近成功读取的内容。${errorMessage(query.error)}`
							: errorMessage(query.error)
					}
					retry={() => void query.refetch()}
				/>
			)}
			{query.data && open && (
				<div id="recent-endings-body">
					<p className="footnote">
						统计北京时间 {fullTime(query.data.window_start)}{" "}
						起结束的委托，含今天在内共 3 个自然日。
					</p>
					{items.length > 0 && (
						<ul className="recent-endings-list">
							{items.map((item) => {
								const changes = endingChanges(item);
								return (
									<li key={item.order_id} data-ending-id={item.order_id}>
										<div className="recent-ending-top">
											<strong>
												{item.name
													? `${item.name}（${item.symbol}）`
													: item.symbol}
											</strong>
											<span className="recent-ending-badges">
												{fresh.has(item.order_id) && (
													<Badge tone="warn">新</Badge>
												)}
												<Badge
													tone={item.status === "filled" ? "good" : "neutral"}
												>
													{endingStatus[item.status]}
												</Badge>
											</span>
										</div>
										<p>
											<span className={`order-direction ${item.side}`}>
												{item.side === "buy" ? "买入" : "卖出"} {item.quantity}{" "}
												股 / 份
											</span>
											{item.fill_price && (
												<span>
													{" "}
													成交价 {price(item.fill_price, item.currency)}
												</span>
											)}
										</p>
										<small>结束于 {fullTime(item.ended_at)}（北京时间）</small>
										{item.status_reason && <small>{item.status_reason}</small>}
										<ul
											className="recent-ending-changes"
											aria-label="资金或持仓变化"
										>
											{changes.length === 0 ? (
												<li>没有资金或持仓变化</li>
											) : (
												changes.map((line) => <li key={line}>{line}</li>)
											)}
										</ul>
										<button
											type="button"
											className="text-button"
											onClick={() => onLocate(item.order_id)}
										>
											在委托记录中查看
										</button>
									</li>
								);
							})}
						</ul>
					)}
					{query.data.more && <p className="notice">{RECENT_ENDINGS_MORE}</p>}
				</div>
			)}
		</Panel>
	);
}
