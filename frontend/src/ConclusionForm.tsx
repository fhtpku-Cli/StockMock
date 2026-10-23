import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { ApiError, api, errorMessage } from "./api";
import {
	type Coverage,
	conclusionResultSchema,
	type ReviewDetailData,
	type ReviewTarget,
	reviewDetailSchema,
	targetPath,
} from "./reviewSchema";
import { ErrorNotice } from "./ui";
import { useOnline } from "./useOnline";

type Submission = {
	readonly request_id: string;
	readonly round_id: string;
	readonly conclusion: string;
	readonly deviation: string;
	readonly next_action: string;
	readonly coverage: Coverage;
};
export function ConclusionForm({
	detail,
	target,
}: {
	readonly detail: ReviewDetailData;
	readonly target: ReviewTarget;
}) {
	const online = useOnline();
	const client = useQueryClient();
	const [conclusion, setConclusion] = useState("");
	const [deviation, setDeviation] = useState("");
	const [nextAction, setNextAction] = useState("");
	const [coverage, setCoverage] = useState(detail.coverage);
	const [pending, setPending] = useState<Submission | null>(null);
	const [conflict, setConflict] = useState(false);
	const [readOnly, setReadOnly] = useState(detail.read_only);
	const refresh = useMutation({
		mutationFn: () =>
			api(
				`${targetPath(target)}?round_id=${encodeURIComponent(target.roundId)}`,
				reviewDetailSchema,
			),
		onSuccess: async (data) => {
			setCoverage(data.coverage);
			setReadOnly(data.read_only);
			setConflict(false);
			setPending(null);
			save.reset();
			client.setQueryData(
				["review-detail", target.roundId, target.subjectType, target.subjectId],
				data,
			);
			await client.invalidateQueries({
				queryKey: [
					"review-facts",
					target.roundId,
					target.subjectType,
					target.subjectId,
				],
			});
		},
	});
	const save = useMutation({
		mutationFn: (body: Submission) =>
			api(`${targetPath(target)}/conclusions`, conclusionResultSchema, body),
		onSuccess: async (data) => {
			setPending(null);
			setConclusion("");
			setDeviation("");
			setNextAction("");
			setReadOnly(data.read_only);
			setCoverage(data.review.coverage);
			await Promise.all(
				["reviews", "review-detail", "review-facts"].map((key) =>
					client.invalidateQueries({ queryKey: [key] }),
				),
			);
		},
		onError: (error) => {
			if (error instanceof ApiError && error.status && error.status < 500) {
				setPending(null);
				if (error.status === 409) setConflict(true);
			}
		},
	});
	const locked = pending !== null;
	return (
		<section className="conclusion-form">
			<h3>{detail.conclusions.length ? "补充复盘" : "保存本次复盘结论"}</h3>
			<p className="notice">
				本次覆盖：
				{coverage.plan_version === null
					? "无计划旧委托"
					: `计划第 ${coverage.plan_version} 版`}{" "}
				· {coverage.filled_order_ids.length} 笔已确认成交 ·{" "}
				{coverage.due_review_date
					? `已到复盘日 ${coverage.due_review_date}`
					: "尚无已到达的复盘日"}
				。覆盖来自完整事实，不受下方分页影响。
			</p>
			{readOnly ? (
				<p className="notice">
					本轮已归档，只能阅读和下载。未保存的草稿仍保留在下面。
				</p>
			) : null}
			<form
				onSubmit={(event) => {
					event.preventDefault();
					if (!online || conflict || readOnly) return;
					const body = pending ?? {
						request_id: crypto.randomUUID(),
						round_id: target.roundId,
						conclusion,
						deviation,
						next_action: nextAction,
						coverage,
					};
					setPending(body);
					save.mutate(body);
				}}
			>
				<label className="field">
					本次结论（必填，最多 2000 字）
					<textarea
						aria-label="本次结论"
						required
						maxLength={2000}
						value={conclusion}
						disabled={locked}
						onChange={(event) => setConclusion(event.target.value)}
					/>
				</label>
				<label className="field">
					与原计划的偏差
					<textarea
						aria-label="与原计划的偏差"
						maxLength={2000}
						value={deviation}
						disabled={locked}
						onChange={(event) => setDeviation(event.target.value)}
					/>
				</label>
				<label className="field">
					下次怎么做
					<textarea
						aria-label="下次怎么做"
						maxLength={2000}
						value={nextAction}
						disabled={locked}
						onChange={(event) => setNextAction(event.target.value)}
					/>
				</label>
				{!online && (
					<p role="status" className="notice">
						当前离线，草稿保留；联网后可重试。
					</p>
				)}
				{locked && !save.isPending && (
					<p className="notice">
						保存结果尚未确认，已保留原提交。请重试确认，避免重复保存。
					</p>
				)}
				{conflict && (
					<p className="notice">
						事实已变化，草稿已保留。请刷新覆盖事实后，核对变化再保存。
					</p>
				)}
				{save.isError && <ErrorNotice message={errorMessage(save.error)} />}
				{refresh.isError && (
					<ErrorNotice message={errorMessage(refresh.error)} />
				)}
				<div className="button-row">
					{!readOnly && (
						<button
							className="button primary"
							type="submit"
							disabled={
								!online ||
								save.isPending ||
								refresh.isPending ||
								conflict ||
								!conclusion.trim()
							}
						>
							{save.isPending
								? "保存中…"
								: locked
									? "重试确认结论结果"
									: "保存本次复盘结论"}
						</button>
					)}
					{conflict && (
						<button
							className="button"
							type="button"
							disabled={!online || refresh.isPending}
							onClick={() => refresh.mutate()}
						>
							{refresh.isPending ? "刷新中…" : "刷新事实并保留草稿"}
						</button>
					)}
				</div>
				{save.isSuccess && (
					<p role="status" className="positive">
						正式结论已保存，原结论继续保留。
					</p>
				)}
			</form>
		</section>
	);
}
