import { useQuery } from "@tanstack/react-query";
import { z } from "zod";
import { AdjustmentRecords } from "./Adjustments";
import { Alerts } from "./Alerts";
import { AssetHistory } from "./AssetHistory";
import { api, errorMessage, money, time } from "./api";
import { CashStatement } from "./CashStatement";
import { LinkedTrade, OrderRecord } from "./OrderRecord";
import { Pagination } from "./Pagination";
import { PlanSummary } from "./PlanSummary";
import { planSchema } from "./planSchema";
import { Reviews } from "./Reviews";
import { decimal, journalSchema, orderSchema } from "./schema";
import { Badge, ErrorNotice, Loading, Modal } from "./ui";
import { usePages } from "./usePages";

const archiveSchema = z.object({
	id: z.string(),
	created_at: z.string(),
	archived_at: z.string().nullable(),
	cash: z.record(z.string(), decimal),
});
export function Archive({
	roundId,
	onClose,
}: {
	readonly roundId: string;
	readonly onClose: () => void;
}) {
	const base = `rounds/${encodeURIComponent(roundId)}`;
	const query = useQuery({
		queryKey: ["round", roundId],
		queryFn: () => api(base, archiveSchema),
	});
	const orders = usePages(
		["archive-orders", roundId],
		`${base}/orders`,
		orderSchema,
	);
	const notes = usePages(
		["archive-journal", roundId],
		`${base}/journal`,
		journalSchema.element,
	);
	const plans = usePages(
		["plans", "archive", roundId],
		`plans?round_id=${encodeURIComponent(roundId)}`,
		planSchema,
	);
	return (
		<Modal title="已归档练习 · 只读" onClose={onClose} wide>
			{query.isPending ? (
				<Loading />
			) : query.isError ? (
				<ErrorNotice
					message={errorMessage(query.error)}
					retry={() => void query.refetch()}
				/>
			) : (
				<div className="stack">
					<div>
						<Badge>历史轮次</Badge>
						<p className="footnote">
							开始 {time(query.data.created_at)} · 归档{" "}
							{time(query.data.archived_at)}（北京时间）
						</p>
						<p className="notice">
							这是此前练习的历史记录，不影响当前账户，也不能在此修改或交易。各类记录可分批加载至全部，导出包含完整历史。
						</p>
					</div>
					<AssetHistory roundId={roundId} />
					<AdjustmentRecords roundId={roundId} />
					<a className="text-button" href={`/api/${base}/export.csv`} download>
						导出本轮完整资金流水
					</a>
					<section>
						<h3>归档时现金余额</h3>
						<dl className="summary-list">
							{Object.entries(query.data.cash).map(([currency, balance]) => (
								<div key={currency}>
									<dt>{currency}</dt>
									<dd>{money(balance, currency)}</dd>
								</div>
							))}
						</dl>
					</section>
					<Reviews roundId={roundId} />
					<details>
						<summary>全部历史计划（含草稿）</summary>
						{plans.isPending ? (
							<Loading />
						) : (
							plans.isError && (
								<ErrorNotice
									message={errorMessage(plans.error)}
									retry={() => void plans.refetch()}
								/>
							)
						)}
						<div className="plan-list">
							{plans.items.map((plan) => (
								<article className="plan-record" key={plan.id}>
									<PlanSummary plan={plan} />
								</article>
							))}
						</div>
						{plans.data && (
							<Pagination
								count={plans.items.length}
								total={plans.total}
								pending={plans.isFetchingNextPage}
								more={plans.hasNextPage}
								onMore={() => void plans.fetchNextPage()}
							/>
						)}
					</details>
					<Alerts roundId={roundId} />
					<section>
						<h3>历史委托（{orders.total}）</h3>
						{orders.isPending ? (
							<Loading />
						) : orders.isError ? (
							<ErrorNotice
								message={errorMessage(orders.error)}
								retry={() => void orders.refetch()}
							/>
						) : (
							orders.total === 0 && <p className="footnote">本轮没有委托。</p>
						)}
						{orders.items.map((order) => (
							<OrderRecord key={order.id} order={order} />
						))}
						{orders.data && (
							<Pagination
								count={orders.items.length}
								total={orders.total}
								pending={orders.isFetchingNextPage}
								more={orders.hasNextPage}
								onMore={() => void orders.fetchNextPage()}
							/>
						)}
					</section>
					<section>
						<h3>历史复盘笔记（{notes.total}）</h3>
						{notes.isPending ? (
							<Loading />
						) : notes.isError ? (
							<ErrorNotice
								message={errorMessage(notes.error)}
								retry={() => void notes.refetch()}
							/>
						) : (
							notes.total === 0 && (
								<p className="footnote">本轮没有复盘笔记。</p>
							)
						)}
						<div className="journal-list">
							{notes.items.map((entry) => (
								<article key={entry.id}>
									<small>
										{time(entry.created_at)}（北京时间）
										{entry.plan_id
											? " · 关联计划"
											: entry.order_id
												? " · 关联委托"
												: " · 独立笔记"}
									</small>
									<p className="review-notes">{entry.text}</p>
									<LinkedTrade
										linked={entry.order_id !== null}
										order={orders.items.find(
											(order) => order.id === entry.order_id,
										)}
									/>
								</article>
							))}
						</div>
						{notes.data && (
							<Pagination
								count={notes.items.length}
								total={notes.total}
								pending={notes.isFetchingNextPage}
								more={notes.hasNextPage}
								onMore={() => void notes.fetchNextPage()}
							/>
						)}
					</section>
					<CashStatement roundId={roundId} />
				</div>
			)}
		</Modal>
	);
}
