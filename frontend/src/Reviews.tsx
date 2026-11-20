import { useInfiniteQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { ApiError, api, errorMessage, time } from "./api";
import { CsvDownload } from "./CsvDownload";
import { Pagination } from "./Pagination";
import { ReviewModal } from "./ReviewDetail";
import {
	filterParams,
	initialReviewFilter,
	ReviewFilters,
} from "./ReviewFilters";
import {
	type ReviewTarget,
	reviewCursorSchema,
	reviewStateNames,
} from "./reviewSchema";
import { Badge, Empty, ErrorNotice, Loading, Panel } from "./ui";
export function Reviews({ roundId }: { readonly roundId?: string }) {
	const [draft, setDraft] = useState(initialReviewFilter);
	const [filter, setFilter] = useState(initialReviewFilter);
	const [state, setState] = useState(roundId ? "all" : "todo");
	const [generation, setGeneration] = useState(0);
	const [selected, setSelected] = useState<ReviewTarget | null>(null);
	const [roundNotice, setRoundNotice] = useState(false);
	const params = filterParams(filter, roundId);
	params.set("review_state", state);
	const query = useInfiniteQuery({
		queryKey: ["reviews", roundId ?? "current", params.toString(), generation],
		initialPageParam: { cursor: "", roundId: "" },
		queryFn: async ({ pageParam }) => {
			const page = await api(
				`reviews/page?${params}&limit=25${pageParam.cursor ? `&cursor=${encodeURIComponent(pageParam.cursor)}` : ""}`,
				reviewCursorSchema,
			);
			if (pageParam.roundId && page.round_id !== pageParam.roundId)
				throw new ApiError(
					"round_changed",
					"练习轮次已变化，请刷新列表；旧轮记录不会拼入新轮。",
				);
			return page;
		},
		getNextPageParam: (last) =>
			last.next_cursor
				? { cursor: last.next_cursor, roundId: last.round_id }
				: undefined,
		refetchOnWindowFocus: false,
	});
	useEffect(() => {
		if (
			query.error instanceof ApiError &&
			(query.error.code === "round_changed" ||
				(query.isFetchNextPageError && query.error.status === 409))
		) {
			setSelected(null);
			setRoundNotice(true);
			setGeneration((value) => value + 1);
		}
	}, [query.error, query.isFetchNextPageError]);
	const first = query.data?.pages[0];
	const items = Array.from(
		new Map(
			query.data?.pages
				.flatMap((page) => page.items)
				.map((item) => [`${item.subject_type}:${item.id}`, item]) ?? [],
		).values(),
	);
	const refresh = () => {
		setSelected(null);
		setRoundNotice(false);
		setGeneration((value) => value + 1);
	};
	return (
		<Panel title={roundId ? "历史计划与交易复盘" : "计划与交易复盘"}>
			<fieldset className="segmented review-counts" aria-label="复盘状态">
				{(
					[
						"todo",
						"all",
						"pending",
						"needs_update",
						"reviewed",
						"not_due",
					] as const
				).map((key) => (
					<button
						type="button"
						aria-pressed={state === key}
						key={key}
						onClick={() => {
							setState(key);
							refresh();
						}}
					>
						{key === "todo"
							? "全部待办"
							: key === "all"
								? "全部状态"
								: `${reviewStateNames[key]} · ${first?.counts[key] ?? "—"}`}
					</button>
				))}
			</fieldset>
			<details className="review-filter-options">
				<summary>
					按证券、市场和日期筛选{filterParams(filter).size ? "（已应用）" : ""}
				</summary>
				<ReviewFilters
					value={draft}
					onChange={setDraft}
					onApply={() => {
						setFilter(draft);
						refresh();
					}}
				/>
			</details>
			<div className="review-toolbar">
				<button type="button" className="button" onClick={refresh}>
					刷新复盘列表
				</button>
				<CsvDownload
					path={`plans/export.csv${roundId ? `?round_id=${encodeURIComponent(roundId)}` : ""}`}
					name="plans.csv"
				>
					导出计划
				</CsvDownload>
				<CsvDownload
					path={`reviews/export.csv${roundId ? `?round_id=${encodeURIComponent(roundId)}` : ""}`}
					name="reviews.csv"
				>
					导出完整复盘
				</CsvDownload>
				<CsvDownload
					path={`reviews/export.csv?${params}`}
					name="filtered-reviews.csv"
				>
					导出筛选复盘
				</CsvDownload>
			</div>
			<p className="footnote">
				日期按最近一条记录的北京时间筛选。打开复盘可查看完整历史。
			</p>
			{roundNotice && (
				<ErrorNotice message="练习轮次已变化，已重新读取列表，旧轮记录不会拼入新轮。" />
			)}
			{query.isPending && <Loading />}
			{query.isError && (
				<ErrorNotice message={errorMessage(query.error)} retry={refresh} />
			)}
			{query.isSuccess && items.length === 0 && (
				<Empty title="这个筛选下还没有记录">
					可调整筛选查看其他计划或旧委托。
				</Empty>
			)}
			<div className="review-list">
				{first &&
					items.map((review) => (
						<article
							className="review-card"
							data-review-id={review.id}
							key={`${review.subject_type}:${review.id}`}
						>
							<div className="plan-heading">
								<strong>
									{review.symbol ?? "未知证券"} ·{" "}
									{review.plan ? "计划" : "未关联计划的委托"}
								</strong>
								<Badge
									tone={
										review.review_state === "pending" ||
										review.review_state === "needs_update"
											? "warn"
											: "neutral"
									}
								>
									{reviewStateNames[review.review_state]}
								</Badge>
							</div>
							<p>
								{review.plan?.reason ||
									review.legacy_order?.reason ||
									review.actual_status}
							</p>
							<p className="footnote">
								{review.order_count} 笔委托 · {review.filled_count} 笔成交 ·
								最近记录 {time(review.latest_record_at)}（北京时间）
							</p>
							{review.update_reasons.map((reason) => (
								<p className="footnote" key={reason}>
									{reason}
								</p>
							))}
							<button
								type="button"
								className="button small"
								aria-haspopup="dialog"
								onClick={() =>
									setSelected({
										roundId: first.round_id,
										subjectType: review.subject_type,
										subjectId: review.id,
									})
								}
							>
								查看计划与实际结果
							</button>
						</article>
					))}
			</div>
			{first && (
				<Pagination
					count={items.length}
					total={first.total}
					pending={query.isFetchingNextPage}
					more={query.hasNextPage}
					onMore={() => void query.fetchNextPage()}
				/>
			)}
			{selected && (
				<ReviewModal target={selected} onClose={() => setSelected(null)} />
			)}
		</Panel>
	);
}
