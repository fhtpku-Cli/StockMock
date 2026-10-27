import { errorMessage, time } from "./api";
import { Pagination } from "./Pagination";
import { factSchema, type ReviewTarget, targetPath } from "./reviewSchema";
import { ErrorNotice, Loading } from "./ui";
import { useRoundPages } from "./useRoundPages";

const labels = {
	order_submitted: "委托提交",
	order_filled: "已确认成交",
	cancel_requested: "撤单申请",
	order_terminal: "委托终态确认",
	plan_created: "计划创建",
	plan_updated: "当前版本更新时间",
	alert: "实际提示",
	note: "普通笔记",
	conclusion: "正式结论",
} as const;
export function ReviewFacts({ target }: { readonly target: ReviewTarget }) {
	const query = useRoundPages(
		["review-facts", target.roundId, target.subjectType, target.subjectId],
		`${targetPath(target)}/facts?round_id=${encodeURIComponent(target.roundId)}`,
		factSchema,
	);
	return (
		<section className="review-facts">
			<h3>已记录事实（{query.total}）</h3>
			<p className="footnote">
				按系统记录时间排列，均为北京时间。市场样本时间单独显示；未保存的计划修改历史和终态时间不会补造。
			</p>
			{query.isPending && <Loading />}
			{query.isError && (
				<ErrorNotice
					message={errorMessage(query.error)}
					retry={() => void query.refetch()}
				/>
			)}
			{query.items.map((fact, index) => (
				<article key={fact.id} data-fact-id={fact.id}>
					{fact.occurred_at === null &&
						(index === 0 || query.items[index - 1]?.occurred_at !== null) && (
							<h4>时间未完整记录</h4>
						)}
					<strong>{labels[fact.type]}</strong>
					<p className="footnote">
						{fact.occurred_at
							? `系统记录 ${time(fact.occurred_at)}`
							: "时间未完整记录 · 系统确认时间未知"}
						{fact.market_time ? ` · 市场时间 ${time(fact.market_time)}` : ""}
						（北京时间）
					</p>
					{fact.order && (
						<p>
							{fact.order.symbol} ·{" "}
							{fact.order.side === "buy" ? "买入" : "卖出"}{" "}
							{fact.order.quantity} 股 / 份 ·{" "}
							{fact.order.reason || "理由未记录"}
							{!fact.order.confirmed_at && fact.order.status !== "accepted"
								? " · 确认时间未记录"
								: ""}
						</p>
					)}
					{(fact.type === "order_filled" || fact.type === "order_terminal") &&
						fact.order?.status_reason && (
							<p className="footnote">{fact.order.status_reason}</p>
						)}
					{fact.alert && (
						<>
							<p>
								计划第 {fact.alert.plan_version} 版 · {fact.alert.message}
							</p>
							<p className="footnote">
								观察于 {time(fact.alert.created_at)} · 价格{" "}
								{fact.alert.price ?? "未知"} ·{" "}
								{fact.alert.source ?? "来源未记录"} · 已读不代表已执行或已复盘
							</p>
						</>
					)}
					{fact.note && <p>{fact.note.text}</p>}
					{fact.conclusion && <p>{fact.conclusion.conclusion}</p>}
					{fact.plan && (
						<p>
							计划第 {fact.plan.version} 版 · {fact.plan.reason}
						</p>
					)}
				</article>
			))}
			{query.data && (
				<Pagination
					count={query.items.length}
					total={query.total}
					pending={query.isFetchingNextPage}
					more={query.hasNextPage}
					onMore={() => void query.fetchNextPage()}
				/>
			)}
		</section>
	);
}
