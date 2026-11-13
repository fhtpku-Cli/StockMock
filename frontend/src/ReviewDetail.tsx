import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import type { z } from "zod";
import { api, errorMessage, money, time } from "./api";
import { ConclusionForm } from "./ConclusionForm";
import type { alertSchema } from "./planSchema";
import { ReviewCurrentPlan } from "./ReviewCurrentPlan";
import { ReviewFacts } from "./ReviewFacts";
import { ReviewOrders } from "./ReviewOrders";
import {
	type ReviewDetailData,
	type ReviewTarget,
	reviewDetailSchema,
	reviewStateNames,
	targetPath,
} from "./reviewSchema";
import { acknowledgmentSchema } from "./schema";
import { Badge, ErrorNotice, Loading, Modal } from "./ui";
import { useOnline } from "./useOnline";
export function ReviewModal({
	target,
	alert,
	onClose,
}: {
	readonly target: ReviewTarget;
	readonly alert?: z.infer<typeof alertSchema>;
	readonly onClose: () => void;
}) {
	return (
		<Modal title="相关计划与复盘" onClose={onClose} wide>
			<button className="text-button" type="button" onClick={onClose}>
				返回来处
			</button>
			<ReviewDetail target={target} {...(alert ? { alert } : {})} />
		</Modal>
	);
}
export function ReviewDetail({
	target,
	alert,
}: {
	readonly target: ReviewTarget;
	readonly alert?: z.infer<typeof alertSchema>;
}) {
	const query = useQuery({
		queryKey: [
			"review-detail",
			target.roundId,
			target.subjectType,
			target.subjectId,
		],
		queryFn: () =>
			api(
				`${targetPath(target)}?round_id=${encodeURIComponent(target.roundId)}`,
				reviewDetailSchema,
			),
		refetchOnWindowFocus: false,
		staleTime: 0,
	});
	return (
		<div className="review-detail">
			{alert && (
				<section className="notice" aria-label="原始提示背景">
					<h3>原始提示背景 · 计划第 {alert.plan_version} 版</h3>
					<p>{alert.message}</p>
					<p>
						条件：
						{alert.condition === "review_due"
							? "到复盘日"
							: alert.condition === "target"
								? "到目标参考价"
								: "到止损参考价"}{" "}
						· 价格 {alert.price ?? "未记录"} · {alert.source ?? "来源未记录"}
					</p>
					<p>
						市场时间 {time(alert.market_time)} · 观察时间{" "}
						{time(alert.created_at)}（北京时间）
					</p>
					<p>
						只展示提示当时已保存的背景；当前计划不是当时全文。查看不会自动标为已读。
					</p>
				</section>
			)}
			{query.isPending ? (
				<Loading />
			) : query.isError ? (
				<ErrorNotice
					message={`无法打开这条复盘：${errorMessage(query.error)}`}
					retry={() => void query.refetch()}
				/>
			) : (
				<DetailContent
					key={JSON.stringify([
						target.roundId,
						target.subjectType,
						target.subjectId,
					])}
					detail={query.data}
					target={target}
				/>
			)}
		</div>
	);
}
function DetailContent({
	detail,
	target,
}: {
	readonly detail: ReviewDetailData;
	readonly target: ReviewTarget;
}) {
	const review = detail.review;
	const plan = review.plan;
	const online = useOnline();
	const client = useQueryClient();
	const [note, setNote] = useState("");
	const [showConclusionForm] = useState(!detail.read_only);
	const saveNote = useMutation({
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
				["journal", "review-detail", "review-facts", "reviews"].map((key) =>
					client.invalidateQueries({ queryKey: [key] }),
				),
			);
		},
	});

	return (
		<>
			<section>
				<Badge>{reviewStateNames[review.review_state]}</Badge>
				{detail.read_only && (
					<p className="notice">已归档 · 只读，状态固定在归档时。</p>
				)}
				{review.update_reasons.map((reason) => (
					<p className="notice" key={reason}>
						{reason}
					</p>
				))}
				{review.notes.length > 0 && !detail.conclusions.length && (
					<p className="footnote">旧笔记已保留，尚未保存正式结论。</p>
				)}
			</section>
			<ReviewCurrentPlan detail={detail} />
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
						<dt>本轮该证券实际持仓</dt>
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
					。关联买卖净份额不是计划专属仓位；这里只汇总已保存的卖单原币净收益，不跨币种相加。部分卖出不代表计划最终结果，旧收益缺失不补为零。
				</p>
			</section>
			<ReviewOrders orders={detail.orders} current={plan} />
			<section className="conclusion-list">
				<h3>历次正式结论（{detail.conclusions.length}）</h3>
				{detail.conclusions.length === 0 && (
					<p className="footnote">尚无正式结论；普通笔记不代表复盘完成。</p>
				)}
				{detail.conclusions.map((entry) => (
					<article key={entry.id}>
						<small>
							{time(entry.created_at)}（北京时间） · 覆盖{" "}
							{entry.coverage.filled_order_ids.length} 笔成交
							{entry.coverage.plan_version !== null
								? ` · 计划第 ${entry.coverage.plan_version} 版`
								: ""}
						</small>
						<p className="review-notes">{entry.conclusion}</p>
						{entry.deviation && (
							<p className="review-notes">与原计划的偏差：{entry.deviation}</p>
						)}
						{entry.next_action && (
							<p className="review-notes">下次怎么做：{entry.next_action}</p>
						)}
					</article>
				))}
			</section>
			{showConclusionForm && <ConclusionForm detail={detail} target={target} />}

			<ReviewFacts target={target} />
			<section>
				<h3>普通笔记</h3>
				<p className="footnote">
					普通笔记按已记录事实分页完整展示，不改变正式复盘状态。
				</p>
				{!detail.read_only && (
					<form
						onSubmit={(event) => {
							event.preventDefault();
							if (online && note.trim()) saveNote.mutate();
						}}
					>
						<label className="field">
							补充普通笔记
							<textarea
								aria-label="计划或委托普通笔记"
								maxLength={5000}
								value={note}
								disabled={saveNote.isPending}
								onChange={(event) => setNote(event.target.value)}
							/>
						</label>
						<button
							className="button"
							type="submit"
							disabled={!online || !note.trim() || saveNote.isPending}
						>
							{saveNote.isPending ? "保存中…" : "保存普通笔记"}
						</button>
						{saveNote.isError && (
							<ErrorNotice message={errorMessage(saveNote.error)} />
						)}
						{saveNote.isSuccess && (
							<p role="status">普通笔记已保存，正式复盘状态不变。</p>
						)}
					</form>
				)}
			</section>
		</>
	);
}
