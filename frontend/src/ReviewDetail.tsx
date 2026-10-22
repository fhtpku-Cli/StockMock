import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, errorMessage, money, time } from "./api";
import { OrderRecord } from "./OrderRecord";
import { Pagination } from "./Pagination";
import { PlanSummary } from "./PlanSummary";
import type { Review } from "./planSchema";
import { planSchema } from "./planSchema";
import { acknowledgmentSchema, orderSchema } from "./schema";
import { ErrorNotice, Loading } from "./ui";
import { useOnline } from "./useOnline";
import { usePages } from "./usePages";

export function ReviewDetail({
	review,
	readOnly,
}: {
	readonly review: Review;
	readonly readOnly: boolean;
}) {
	const online = useOnline();
	const client = useQueryClient();
	const [note, setNote] = useState("");
	const [ending, setEnding] = useState(false);
	const plan = review.plan;
	const orders = usePages(
		["plan-orders", plan?.id ?? "none"],
		`plans/${encodeURIComponent(plan?.id ?? "")}/orders`,
		orderSchema,
		!!plan,
	);
	const save = useMutation({
		mutationFn: () =>
			api("journal", acknowledgmentSchema, {
				text: note,
				...(plan
					? { plan_id: plan.id }
					: { order_id: review.legacy_order?.id }),
			}),
		onSuccess: async () => {
			setNote("");
			await Promise.all(
				["reviews", "journal"].map((key) =>
					client.invalidateQueries({ queryKey: [key] }),
				),
			);
		},
	});
	const end = useMutation({
		mutationFn: () =>
			api(
				`plans/${encodeURIComponent(plan?.id ?? "")}`,
				planSchema,
				plan
					? {
							symbol: plan.symbol,
							direction: plan.direction,
							status: "ended",
							reason: plan.reason,
							target_price: plan.target_price,
							stop_price: plan.stop_price,
							review_date: plan.review_date,
							invalidation: plan.invalidation,
							version: plan.version,
						}
					: {},
				{ method: "put" },
			),
		onSuccess: async () => {
			setEnding(false);
			await Promise.all(
				["reviews", "plans", "alerts"].map((key) =>
					client.invalidateQueries({ queryKey: [key] }),
				),
			);
		},
	});
	return (
		<div className="review-detail">
			{plan && (
				<section>
					<h3>当前计划</h3>
					<PlanSummary plan={plan} />
					{!readOnly && plan.status !== "ended" && (
						<>
							{ending ? (
								<div className="notice">
									<p>
										结束后本计划内容只读，未完成的委托不会自动撤销，持仓不会自动卖出。
									</p>
									<div className="button-row">
										<button
											type="button"
											className="button"
											disabled={end.isPending}
											onClick={() => setEnding(false)}
										>
											继续保留
										</button>
										<button
											type="button"
											className="button primary"
											disabled={!online || end.isPending}
											onClick={() => end.mutate()}
										>
											{end.isPending ? "结束中…" : "确认结束计划"}
										</button>
									</div>
								</div>
							) : (
								<button
									type="button"
									className="button small"
									disabled={!online}
									onClick={() => setEnding(true)}
								>
									结束本计划
								</button>
							)}
							{end.isError && (
								<ErrorNotice
									message={errorMessage(end.error)}
									retry={() =>
										void client.invalidateQueries({ queryKey: ["reviews"] })
									}
								/>
							)}
						</>
					)}
				</section>
			)}
			<section>
				<h3>实际结果 · {review.currency}</h3>
				<dl className="summary-list">
					<div>
						<dt>关联买入 / 卖出份额</dt>
						<dd>
							{review.bought_quantity} / {review.sold_quantity}
						</dd>
					</div>
					<div>
						<dt>关联买卖净份额</dt>
						<dd>{review.remaining_quantity}</dd>
					</div>
					<div>
						<dt>本轮该标的实际持仓</dt>
						<dd>{review.account_quantity}</dd>
					</div>
					<div>
						<dt>关联卖单原币净收益</dt>
						<dd>
							{review.realized_pnl === null
								? "尚不能确认"
								: money(review.realized_pnl, review.currency)}
						</dd>
					</div>
				</dl>
				<p className="notice">
					{review.actual_status}
					。这里只汇总已成交卖出份额、扣除买卖费用后的收益；部分卖出后的剩余持仓不计入这一结果，也不代表整个计划最终收益。
				</p>
			</section>
			<section>
				<h3>委托当时的计划与成交</h3>
				{plan ? (
					<>
						{orders.isPending ? (
							<Loading />
						) : (
							orders.isError && (
								<ErrorNotice
									message={errorMessage(orders.error)}
									retry={() => void orders.refetch()}
								/>
							)
						)}
						{orders.isSuccess && orders.items.length === 0 && (
							<p className="footnote">还没有关联委托。</p>
						)}
						{orders.items.map((order) => (
							<OrderRecord key={order.id} order={order} expanded />
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
					</>
				) : (
					review.legacy_order && (
						<OrderRecord order={review.legacy_order} expanded />
					)
				)}
			</section>
			<section>
				<h3>复盘笔记</h3>
				{review.notes.length === 0 ? (
					<p className="footnote">
						未记录。
						{review.filled_count === 0
							? "尚未成交，也可以写下观察或撤单的判断。"
							: "可对照当时计划写下你的收获。"}
					</p>
				) : (
					<div className="journal-list">
						{review.notes.map((entry) => (
							<article key={entry.id}>
								<small>{time(entry.created_at)}（北京时间）</small>
								<p className="review-notes">{entry.text}</p>
							</article>
						))}
					</div>
				)}
				{!readOnly && (
					<form
						onSubmit={(event) => {
							event.preventDefault();
							if (online && note.trim()) save.mutate();
						}}
					>
						<label className="field">
							补充复盘笔记
							<textarea
								aria-label="计划或委托复盘内容"
								value={note}
								onChange={(event) => setNote(event.target.value)}
								maxLength={5000}
								required
								disabled={save.isPending || !online}
							/>
						</label>
						<button
							type="submit"
							className="button primary"
							disabled={!online || save.isPending || !note.trim()}
						>
							{save.isPending ? "保存中…" : "保存复盘"}
						</button>
						{save.isError && <ErrorNotice message={errorMessage(save.error)} />}
						{save.isSuccess && (
							<p role="status" className="positive">
								复盘已保存，可在“已复盘”中查看。
							</p>
						)}
					</form>
				)}
			</section>
		</div>
	);
}
