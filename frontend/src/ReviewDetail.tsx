import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useLayoutEffect, useRef, useState } from "react";
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
import { acknowledgmentSchema, type Order, roundsSchema } from "./schema";
import { Badge, ErrorNotice, Loading, Modal } from "./ui";
import { useOnline } from "./useOnline";
export function OrderReviewModal({
	order,
	onClose,
}: {
	readonly order: Order;
	readonly onClose: () => void;
}) {
	const round = useQuery({
		queryKey: ["order-review-round", order.id],
		queryFn: async () => {
			if (order.plan_snapshot && order.plan_snapshot.id === order.plan_id)
				return order.plan_snapshot.round_id;
			const rounds = await api("rounds", roundsSchema);
			const current = rounds.find((item) => !item.archived_at);
			if (!current) throw new Error("找不到当前练习轮次。");
			return current.id;
		},
		staleTime: Infinity,
		refetchOnWindowFocus: false,
	});
	return (
		<Modal title="相关计划与复盘" onClose={onClose} wide>
			{round.isPending ? (
				<Loading />
			) : round.isError ? (
				<ErrorNotice
					message={errorMessage(round.error)}
					retry={() => void round.refetch()}
				/>
			) : (
				<ReviewDetail
					target={{
						roundId: round.data,
						subjectType: order.plan_id ? "plan" : "order",
						subjectId: order.plan_id ?? order.id,
						orderId: order.id,
					}}
				/>
			)}
		</Modal>
	);
}
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
			) : target.orderId &&
				!query.data.orders.some((order) => order.id === target.orderId) ? (
				<ErrorNotice
					message="这笔委托不在该复盘记录中，请返回委托列表重新选择。"
					retry={() => void query.refetch()}
				/>
			) : (
				<DetailContent
					key={JSON.stringify([
						target.roundId,
						target.subjectType,
						target.subjectId,
						target.orderId,
					])}
					detail={query.data}
					target={target}
				/>
			)}
		</div>
	);
}
const reviewSections = [
	"计划与结果",
	"委托依据",
	"正式结论",
	"事实与笔记",
] as const;
function DetailContent({
	detail,
	target,
}: {
	readonly detail: ReviewDetailData;
	readonly target: ReviewTarget;
}) {
	const [section, setSection] = useState<(typeof reviewSections)[number]>(
		target.orderId ? "委托依据" : "计划与结果",
	);
	const navigation = useRef<HTMLFieldSetElement>(null);
	useLayoutEffect(() => {
		const tabs = navigation.current;
		const heading = tabs?.closest("dialog")?.querySelector(".modal-heading");
		if (!tabs || !heading) return;
		const align = () => {
			tabs.style.top = `${heading.getBoundingClientRect().height}px`;
		};
		align();
		const observer = new ResizeObserver(align);
		observer.observe(heading);
		return () => observer.disconnect();
	}, []);
	const review = detail.review;
	const plan = review.plan;
	const online = useOnline();
	const client = useQueryClient();
	const [note, setNote] = useState("");

	const saveNote = useMutation({
		mutationFn: () =>
			api("journal", acknowledgmentSchema, {
				text: note,
				...(target.orderId
					? { order_id: target.orderId }
					: plan
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
				<div className="plan-heading">
					<strong>
						{review.symbol ?? "未知证券"} · {plan ? "计划复盘" : "委托复盘"}
					</strong>
					<Badge>{reviewStateNames[review.review_state]}</Badge>
				</div>
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
			<fieldset
				className="segmented review-sections"
				aria-label="复盘详情内容"
				ref={navigation}
			>
				{reviewSections.map((label) => (
					<button
						type="button"
						key={label}
						aria-pressed={section === label}
						onClick={() => {
							setSection(label);
							navigation.current?.closest("dialog")?.scrollTo({ top: 0 });
						}}
					>
						{label}
					</button>
				))}
			</fieldset>
			<div className="review-section" hidden={section !== "计划与结果"}>
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
						。部分卖出不代表计划最终结果。
					</p>
					<details>
						<summary>份额与收益口径</summary>
						<p>
							计划关联的买卖净额与账户实际持仓分别统计。收益只合计已记录的原币卖单净收益，旧记录缺失时显示未知。
						</p>
					</details>
				</section>
			</div>
			<div className="review-section" hidden={section !== "委托依据"}>
				<ReviewOrders
					orders={detail.orders}
					current={plan}
					initialOrderId={target.orderId}
				/>
			</div>
			<div className="review-section" hidden={section !== "正式结论"}>
				<ConclusionForm detail={detail} target={target} />
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
								<p className="review-notes">
									与原计划的偏差：{entry.deviation}
								</p>
							)}
							{entry.next_action && (
								<p className="review-notes">下次怎么做：{entry.next_action}</p>
							)}
						</article>
					))}
				</section>
			</div>
			<div className="review-section" hidden={section !== "事实与笔记"}>
				<section>
					<h3>普通笔记</h3>
					<p className="footnote">
						{target.orderId
							? `笔记关联委托 ${target.orderId.slice(0, 8)}。`
							: ""}
						普通笔记保留在事实记录中，不改变复盘状态。
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
				<ReviewFacts target={target} />
			</div>
		</>
	);
}
