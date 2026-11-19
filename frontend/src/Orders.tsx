import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { z } from "zod";
import { api, errorMessage } from "./api";
import { OrderRecord } from "./OrderRecord";
import {
	initialRecordFilter,
	RecordFilters,
	recordMatches,
} from "./RecordFilters";
import { OrderReviewModal } from "./ReviewDetail";
import { canReorder } from "./ReorderTrade";
import { type Order, orderSchema } from "./schema";
import { Empty, ErrorNotice, Loading, Modal, Panel } from "./ui";
import { useOnline } from "./useOnline";
export const CANCEL_CONFIRM =
	"撤单申请提交后不能撤回；委托生效后，系统要等撤单前的延迟数据确认，期间仍可能成交。";
export function Orders({
	receipt,
	onReorder,
	initialFilter,
}: {
	readonly receipt?: Order;
	readonly onReorder: (order: Order) => void;
	readonly initialFilter: "all" | "active";
}) {
	const online = useOnline();
	const receiptRef = useRef<HTMLParagraphElement>(null);
	useEffect(() => {
		if (receipt) receiptRef.current?.scrollIntoView({ block: "nearest" });
	}, [receipt]);
	const [reviewOrder, setReviewOrder] = useState<Order | null>(null);
	const [cancelOrder, setCancelOrder] = useState<Order | null>(null);
	const [recordFilter, setRecordFilter] = useState(initialRecordFilter);
	const [filter, setFilter] = useState<string>(initialFilter);
	const client = useQueryClient();
	const query = useQuery({
		queryKey: ["orders"],
		queryFn: ({ signal }) =>
			api("orders", z.array(orderSchema), undefined, { signal }),
		refetchInterval: 15000,
		refetchOnMount: "always",
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
			recordMatches(o.created_at, o.currency, recordFilter) &&
			(filter === "all" ||
				(filter === "active"
					? o.status === "accepted" || o.status === "cancel_pending"
					: o.status === "filled")),
	);
	return (
		<div className="stack">
			{receipt && (
				<p role="status" className="trade-receipt" ref={receiptRef}>
					已收到 {receipt.symbol} 的{receipt.side === "buy" ? "买入" : "卖出"}
					委托 · {receipt.quantity} 股 / 份 · 编号 {receipt.id.slice(0, 8)}
					。最新状态见委托记录。
				</p>
			)}
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
				<RecordFilters value={recordFilter} onChange={setRecordFilter} />
				<fieldset className="segmented" aria-label="筛选委托">
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
				</fieldset>
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
							<OrderRecord
								key={o.id}
								order={o}
								actions={
									<>
										<button
											type="button"
											className="text-button"
											onClick={() => setReviewOrder(o)}
										>
											写复盘
										</button>
										{canReorder(o) && (
											<button
												type="button"
												className="button small"
												disabled={!online}
												onClick={() => onReorder(o)}
											>
												按原参数再下单
											</button>
										)}
										{o.status === "accepted" && (
											<button
												type="button"
												className="button small"
												disabled={!online || cancel.isPending}
												onClick={() => setCancelOrder(o)}
											>
												{cancel.isPending ? "处理中…" : "申请撤单"}
											</button>
										)}
									</>
								}
							/>
						))}
					</div>
				)}
			</Panel>
			{reviewOrder && (
				<OrderReviewModal
					order={reviewOrder}
					onClose={() => setReviewOrder(null)}
				/>
			)}
			{cancelOrder && (
				<Modal title="确认申请撤单" onClose={() => setCancelOrder(null)}>
					<p>
						{cancelOrder.symbol} ·{" "}
						{cancelOrder.side === "buy" ? "买入" : "卖出"}{" "}
						{cancelOrder.quantity} 股 / 份 · 编号 {cancelOrder.id.slice(0, 8)}
					</p>
					<p className="notice">{CANCEL_CONFIRM}</p>
					<div className="button-row">
						<button
							type="button"
							className="button"
							onClick={() => setCancelOrder(null)}
						>
							保留委托
						</button>
						<button
							type="button"
							className="button danger"
							disabled={
								!online ||
								cancel.isPending ||
								!query.data?.some(
									(order) =>
										order.id === cancelOrder.id && order.status === "accepted",
								)
							}
							onClick={() => {
								cancel.mutate(cancelOrder.id);
								setCancelOrder(null);
							}}
						>
							确认申请撤单
						</button>
					</div>
				</Modal>
			)}
		</div>
	);
}
