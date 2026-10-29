import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { z } from "zod";
import { api, errorMessage } from "./api";
import { OrderRecord } from "./OrderRecord";
import {
	initialRecordFilter,
	RecordFilters,
	recordMatches,
} from "./RecordFilters";
import { canReorder } from "./ReorderTrade";
import { type Order, orderSchema } from "./schema";
import { Empty, ErrorNotice, Loading, Panel } from "./ui";
import { useOnline } from "./useOnline";
export function Orders({
	onJournal,
	onReorder,
}: {
	readonly onJournal: (id: string) => void;
	readonly onReorder: (order: Order) => void;
}) {
	const online = useOnline();
	const [recordFilter, setRecordFilter] = useState(initialRecordFilter);
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
			recordMatches(o.created_at, o.currency, recordFilter) &&
			(filter === "all" ||
				(filter === "active"
					? o.status === "accepted" || o.status === "cancel_pending"
					: o.status === "filled")),
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
											onClick={() => onJournal(o.id)}
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
												onClick={() => cancel.mutate(o.id)}
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
		</div>
	);
}
