import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { ApiError, api, errorMessage } from "./api";
import type { Plan } from "./planSchema";
import { planSchema } from "./planSchema";
import { ErrorNotice } from "./ui";
import { useOnline } from "./useOnline";

export function PlanForm({
	symbol,
	currency,
	plan,
	onSaved,
	onReload,
	onCancel,
}: {
	readonly symbol: string;
	readonly currency: string;
	readonly plan: Plan | null;
	readonly onSaved: () => void;
	readonly onReload: (value: Plan) => void;
	readonly onCancel: () => void;
}) {
	const online = useOnline();
	const client = useQueryClient();
	const [direction, setDirection] = useState<Plan["direction"]>(
		plan?.direction ?? "watch",
	);
	const [status, setStatus] = useState<Plan["status"]>(plan?.status ?? "draft");
	const [reason, setReason] = useState(plan?.reason ?? "");
	const [target, setTarget] = useState(plan?.target_price ?? "");
	const [stop, setStop] = useState(plan?.stop_price ?? "");
	const [date, setDate] = useState(plan?.review_date ?? "");
	const [invalidation, setInvalidation] = useState(plan?.invalidation ?? "");
	const save = useMutation({
		mutationFn: () =>
			api(
				plan ? `plans/${encodeURIComponent(plan.id)}` : "plans",
				planSchema,
				{
					symbol,
					direction,
					status,
					reason,
					target_price: target || null,
					stop_price: stop || null,
					review_date: date || null,
					invalidation,
					...(plan ? { version: plan.version } : {}),
				},
				{ method: plan ? "put" : "post" },
			),
		onSuccess: async () => {
			await Promise.all(
				["plans", "reviews", "alerts"].map((key) =>
					client.invalidateQueries({ queryKey: [key] }),
				),
			);
			onSaved();
		},
	});
	const reload = useMutation({
		mutationFn: () =>
			api(`plans/${encodeURIComponent(plan?.id ?? "")}`, planSchema),
		onSuccess: async (latest) => {
			await client.invalidateQueries({ queryKey: ["plans"] });
			onReload(latest);
		},
	});
	const conflict =
		save.error instanceof ApiError && save.error.code === "plan_conflict";
	return (
		<form
			className="plan-form"
			onSubmit={(event) => {
				event.preventDefault();
				if (online && !conflict) save.mutate();
			}}
		>
			<h3>
				{plan ? "编辑计划" : "新建计划"} · {symbol}
			</h3>
			<fieldset
				disabled={!online || save.isPending || reload.isPending || conflict}
			>
				<div className="form-grid">
					<div className="field">
						<label htmlFor="plan-direction">计划方向</label>
						<select
							id="plan-direction"
							value={direction}
							onChange={(event) =>
								setDirection(
									planSchema.shape.direction.parse(event.target.value),
								)
							}
						>
							<option value="watch">观察</option>
							<option value="buy">准备买入</option>
							<option value="sell">准备卖出</option>
						</select>
					</div>
					<div className="field">
						<label htmlFor="plan-status">计划状态</label>
						<select
							id="plan-status"
							value={status}
							onChange={(event) =>
								setStatus(planSchema.shape.status.parse(event.target.value))
							}
						>
							<option value="draft">草稿</option>
							<option value="active">进行中</option>
							<option value="ended">已结束</option>
						</select>
					</div>
				</div>
				<p className="footnote">
					同一证券同时只能有一个进行中的计划。草稿不产生提示；结束需由你明确选择，不会因为一笔卖出而自动结束。
				</p>
				<div className="field">
					<label htmlFor="plan-reason">
						计划理由 <span>选填</span>
					</label>
					<textarea
						id="plan-reason"
						maxLength={2000}
						value={reason}
						onChange={(event) => setReason(event.target.value)}
						placeholder="记录你的判断依据"
					/>
				</div>
				<div className="form-grid">
					<div className="field">
						<label htmlFor="plan-target">目标参考价（{currency}）</label>
						<input
							id="plan-target"
							type="number"
							inputMode="decimal"
							min="0.0001"
							step="any"
							value={target}
							onChange={(event) => setTarget(event.target.value)}
						/>
					</div>
					<div className="field">
						<label htmlFor="plan-stop">止损参考价（{currency}）</label>
						<input
							id="plan-stop"
							type="number"
							inputMode="decimal"
							min="0.0001"
							step="any"
							value={stop}
							onChange={(event) => setStop(event.target.value)}
						/>
					</div>
				</div>
				<p className="footnote">
					本版按做多方向观察：有效报价 ≥ 目标参考价，或 ≤
					止损参考价时产生站内提示。三个计划方向均使用这一规则，不会自动买卖或撤单。
				</p>
				<div className="field">
					<label htmlFor="plan-date">
						计划复盘日期（北京时间）<span>选填</span>
					</label>
					<input
						id="plan-date"
						type="date"
						value={date}
						onChange={(event) => setDate(event.target.value)}
					/>
				</div>
				<div className="field">
					<label htmlFor="plan-invalidation">
						失效条件 <span>选填</span>
					</label>
					<textarea
						id="plan-invalidation"
						value={invalidation}
						maxLength={2000}
						onChange={(event) => setInvalidation(event.target.value)}
						placeholder="什么情况发生时，需要重新审视这个计划？"
					/>
				</div>
			</fieldset>
			{save.isError && <ErrorNotice message={errorMessage(save.error)} />}
			{conflict && (
				<div className="notice">
					<p>
						另一台设备已修改此计划。为避免覆盖，当前编辑未保存；请重新载入最新内容后再编辑。
					</p>
					<button
						type="button"
						className="button"
						disabled={reload.isPending || !online}
						onClick={() => reload.mutate()}
					>
						{reload.isPending ? "重新载入中…" : "重新载入最新计划"}
					</button>
				</div>
			)}
			{reload.isError && <ErrorNotice message={errorMessage(reload.error)} />}
			<div className="button-row">
				<button
					type="button"
					className="button"
					onClick={onCancel}
					disabled={save.isPending}
				>
					取消编辑
				</button>
				<button
					className="button primary"
					type="submit"
					disabled={!online || save.isPending || conflict}
				>
					{save.isPending ? "保存中…" : "保存计划"}
				</button>
			</div>
		</form>
	);
}
