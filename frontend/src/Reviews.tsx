import { useState } from "react";
import { errorMessage } from "./api";
import { Pagination } from "./Pagination";
import { reviewSchema, reviewStates } from "./planSchema";
import { ReviewDetail } from "./ReviewDetail";
import { Badge, Empty, ErrorNotice, Loading, Panel } from "./ui";
import { usePages } from "./usePages";

export function Reviews({ roundId }: { readonly roundId?: string }) {
	const [status, setStatus] = useState(roundId ? "all" : "pending");
	const [selected, setSelected] = useState<string | null>(null);
	const params = new URLSearchParams({ status });
	if (roundId) params.set("round_id", roundId);
	const query = usePages(
		["reviews", roundId ?? "current", status],
		`reviews?${params}`,
		reviewSchema,
	);
	return (
		<Panel title={roundId ? "历史计划与交易复盘" : "计划与交易复盘"}>
			<div className="review-toolbar">
				<label
					className="sr-only"
					htmlFor={`review-status-${roundId ?? "current"}`}
				>
					复盘状态
				</label>
				<select
					id={`review-status-${roundId ?? "current"}`}
					value={status}
					onChange={(event) => {
						setStatus(event.target.value);
						setSelected(null);
					}}
				>
					<option value="pending">待复盘</option>
					<option value="reviewed">已复盘</option>
					<option value="all">全部（含未成交与旧单）</option>
				</select>
				<a
					className="text-button"
					href={`/api/plans/export.csv${roundId ? `?round_id=${encodeURIComponent(roundId)}` : ""}`}
					download
				>
					导出计划
				</a>
				<a
					className="text-button"
					href={`/api/reviews/export.csv${roundId ? `?round_id=${encodeURIComponent(roundId)}` : ""}`}
					download
				>
					导出复盘
				</a>
			</div>
			<p className="footnote">
				实际成交或到计划复盘日期后才进入待复盘。取消且未成交的委托可在全部中记录撤单判断，不算盈利交易。收益保留原币，不跨币种相加。
			</p>
			{query.isPending ? (
				<Loading />
			) : (
				query.isError && (
					<ErrorNotice
						message={errorMessage(query.error)}
						retry={() => void query.refetch()}
					/>
				)
			)}
			{query.isSuccess && query.items.length === 0 && (
				<Empty
					title={
						status === "pending" ? "暂时没有待复盘记录" : "这个筛选下还没有记录"
					}
				>
					计划可以先保存；成交或到复盘日后再回来对照结果。
				</Empty>
			)}
			<div className="review-list">
				{query.items.map((review) => (
					<article className="review-card" key={review.id}>
						<div className="plan-heading">
							<strong>
								{review.plan?.symbol ??
									review.legacy_order?.symbol ??
									"未记录证券"}{" "}
								· {review.plan ? "计划" : "未关联计划的委托"}
							</strong>
							<Badge tone={review.status === "pending" ? "warn" : "neutral"}>
								{reviewStates[review.status]}
							</Badge>
						</div>
						<p>{review.actual_status}</p>
						<p className="footnote">
							{review.order_count} 笔委托 · {review.filled_count} 笔成交 ·{" "}
							{review.notes.length} 篇笔记
							{review.plan?.review_date
								? ` · 复盘日期 ${review.plan.review_date}（北京时间）`
								: ""}
						</p>
						<button
							type="button"
							className="button small"
							aria-expanded={selected === review.id}
							onClick={() =>
								setSelected(selected === review.id ? null : review.id)
							}
						>
							{selected === review.id ? "收起复盘详情" : "查看计划与实际结果"}
						</button>
						{selected === review.id && (
							<ReviewDetail
								key={review.id}
								review={review}
								readOnly={!!roundId}
							/>
						)}
					</article>
				))}
			</div>
			{query.data && (
				<Pagination
					count={query.items.length}
					total={query.total}
					pending={query.isFetchingNextPage}
					more={query.hasNextPage}
					onMore={() => void query.fetchNextPage()}
				/>
			)}
		</Panel>
	);
}
