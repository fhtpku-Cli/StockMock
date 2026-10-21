import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { z } from "zod";
import { api, errorMessage, money, price, time } from "./api";
import { orderSchema } from "./schema";
import type { Order } from "./schema";
import { Badge, Empty, ErrorNotice, Loading, Panel } from "./ui";
const statuses = {
	accepted: "等待成交",
	cancel_pending: "撤单确认中",
	filled: "已成交",
	cancelled: "已撤单",
	expired: "已过期",
	terminated: "已终止",
} as const;
export function Orders({
	onJournal,
}: {
	readonly onJournal: (id: string) => void;
}) {
	const [filter, setFilter] = useState("all");
	const client = useQueryClient();
	const query = useQuery({
		queryKey: ["orders"],
		queryFn: () => api("orders", z.array(orderSchema)),
		refetchInterval: 15000,
	});
	const cancel = useMutation({
		mutationFn: (id: string) =>
			api(`orders/${encodeURIComponent(id)}/cancel`, orderSchema, {}),
		onSuccess: async () => {
			await Promise.all([
				client.invalidateQueries({ queryKey: ["orders"] }),
				client.invalidateQueries({ queryKey: ["portfolio"] }),
			]);
		},
	});
	const orders = query.data?.filter(
		(o) =>
			filter === "all" ||
			(filter === "active"
				? o.status === "accepted" || o.status === "cancel_pending"
				: o.status === "filled"),
	);
	return (
		<div className="stack">
			<div className="notice">
				委托以有效市场行情判断成交，回执可能延迟。撤单确认前，原占用资金或持仓继续保留。
			</div>
			<Panel
				title="委托记录"
				action={
					<a className="text-button" href="/api/export.csv" download>
						导出流水
					</a>
				}
			>
				<div className="segmented" aria-label="筛选委托">
					{[
						{ id: "all", label: "全部" },
						{ id: "active", label: "进行中" },
						{ id: "filled", label: "已成交" },
					].map((f) => (
						<button
							type="button"
							key={f.id}
							aria-pressed={filter === f.id}
							onClick={() => setFilter(f.id)}
						>
							{f.label}
						</button>
					))}
				</div>
				{cancel.isError && <ErrorNotice message={errorMessage(cancel.error)} />}{" "}
				{query.isPending ? (
					<Loading />
				) : query.isError ? (
					<ErrorNotice
						message={errorMessage(query.error)}
						retry={() => void query.refetch()}
					/>
				) : orders?.length === 0 ? (
					<Empty title="还没有委托记录">
						做好判断后，从标的详情提交第一笔模拟委托。
					</Empty>
				) : (
					<div className="order-list">
						{orders?.map((o) => (
							<OrderRow
								key={o.id}
								order={o}
								busy={cancel.isPending}
								cancel={() => cancel.mutate(o.id)}
								onJournal={() => onJournal(o.id)}
							/>
						))}
					</div>
				)}
			</Panel>
		</div>
	);
}
function OrderRow({
	order: o,
	busy,
	cancel,
	onJournal,
}: {
	readonly order: Order;
	readonly busy: boolean;
	readonly cancel: () => void;
	readonly onJournal: () => void;
}) {
	return (
		<article className="order-row">
			<div className="order-top">
				<div>
					<strong>{o.symbol}</strong>
					<span className={o.side === "buy" ? "positive" : "negative"}>
						{o.side === "buy" ? "买入" : "卖出"} {o.quantity} 股 / 份
					</span>
				</div>
				<Badge
					tone={
						o.status === "filled"
							? "good"
							: o.status === "accepted" || o.status === "cancel_pending"
								? "warn"
								: "neutral"
					}
				>
					{statuses[o.status]}
				</Badge>
			</div>
			<div className="order-details">
				<span>
					{o.order_type === "limit" ? "限价" : "市价模拟"}{" "}
					{price(o.limit_price ?? o.price_bound, o.currency)}
				</span>
				<span>提交 {time(o.created_at)}</span>
				{o.fill_price && <span>成交 {price(o.fill_price, o.currency)}</span>}
				{o.fee && <span>费用 {money(o.fee, o.currency)}</span>}
			</div>
			{o.status_reason && <p className="footnote">{o.status_reason}</p>}
			<details>
				<summary>时间与交易计划</summary>
				<dl className="summary-list">
					<div>
						<dt>生效时间</dt>
						<dd>{time(o.active_at)}</dd>
					</div>
					<div>
						<dt>有效至</dt>
						<dd>{time(o.expires_at)}</dd>
					</div>
					<div>
						<dt>成交时间 / 确认时间</dt>
						<dd>
							{time(o.filled_at)} / {time(o.confirmed_at)}
						</dd>
					</div>
					<div>
						<dt>撤单请求时间</dt>
						<dd>{time(o.cancel_at)}</dd>
					</div>
					<div>
						<dt>交易理由</dt>
						<dd>{o.reason || "未填写"}</dd>
					</div>
					<div>
						<dt>目标 / 止损参考价</dt>
						<dd>
							{price(o.target_price, o.currency)} /{" "}
							{price(o.stop_price, o.currency)}
						</dd>
					</div>
				</dl>
			</details>
			<div className="order-actions">
				<button type="button" className="text-button" onClick={onJournal}>
					写复盘
				</button>
				{o.status === "accepted" && (
					<button
						type="button"
						className="button small"
						disabled={busy}
						onClick={cancel}
					>
						{busy ? "处理中…" : "申请撤单"}
					</button>
				)}
			</div>
		</article>
	);
}
