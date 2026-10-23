import { useState } from "react";
import { errorMessage, time } from "./api";
import { Pagination } from "./Pagination";
import { ReviewModal } from "./ReviewDetail";
import {
	filterParams,
	initialReviewFilter,
	ReviewFilters,
} from "./ReviewFilters";
import { journalEntrySchema, type ReviewTarget } from "./reviewSchema";
import { Empty, ErrorNotice, Loading, Panel } from "./ui";
import { useRoundPages } from "./useRoundPages";
export function JournalNotes({ roundId }: { readonly roundId?: string }) {
	const [draft, setDraft] = useState(initialReviewFilter);
	const [filter, setFilter] = useState(initialReviewFilter);
	const [generation, setGeneration] = useState(0);
	const [target, setTarget] = useState<ReviewTarget | null>(null);
	const params = filterParams(filter, roundId).toString();
	const query = useRoundPages(
		["journal", roundId ?? "current", params, String(generation)],
		`journal/page?${params}`,
		journalEntrySchema,
	);
	return (
		<Panel title={roundId ? "历史复盘笔记" : "普通笔记"}>
			<ReviewFilters
				value={draft}
				onChange={setDraft}
				notes
				onApply={() => {
					setFilter(draft);
					setGeneration((value) => value + 1);
				}}
			/>
			<p className="footnote">
				笔记日期按保存时间筛选，独立于上方复盘的最近记录日期和下方资金核对。证券与市场先取关联计划，再取关联委托；未关联或归属未知仅在全部显示。
			</p>
			<button
				className="text-button"
				type="button"
				onClick={() => setGeneration((value) => value + 1)}
			>
				刷新笔记列表
			</button>
			{query.isPending && <Loading />}
			{query.isError && (
				<ErrorNotice
					message={errorMessage(query.error)}
					retry={() => setGeneration((value) => value + 1)}
				/>
			)}
			{query.isSuccess && query.items.length === 0 && (
				<Empty title="这个筛选下没有普通笔记">
					可清空证券或市场条件查看未关联记录。
				</Empty>
			)}
			<div className="journal-list">
				{query.items.map((entry) => (
					<article key={entry.id}>
						<small>
							{time(entry.created_at)}（北京时间） ·{" "}
							{entry.symbol ?? "证券归属未知 / 未关联"} · 普通笔记
						</small>
						<p className="review-notes">{entry.text}</p>
						{entry.review_subject_id &&
							entry.review_subject_type &&
							query.roundId && (
								<button
									className="button small"
									type="button"
									onClick={() => {
										if (
											entry.review_subject_id &&
											entry.review_subject_type &&
											query.roundId
										)
											setTarget({
												roundId: query.roundId,
												subjectType: entry.review_subject_type,
												subjectId: entry.review_subject_id,
											});
									}}
								>
									查看相关复盘
								</button>
							)}
					</article>
				))}
			</div>
			{query.data && (
				<Pagination
					count={query.items.length}
					total={query.total}
					more={query.hasNextPage}
					pending={query.isFetchingNextPage}
					onMore={() => void query.fetchNextPage()}
				/>
			)}
			{target && (
				<ReviewModal target={target} onClose={() => setTarget(null)} />
			)}
		</Panel>
	);
}
