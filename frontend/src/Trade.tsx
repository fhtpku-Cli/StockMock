import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { z } from "zod";
import { ApiError, api, errorMessage } from "./api";
import { PlanPicker } from "./PlanPicker";
import { type Plan, planSchema } from "./planSchema";
import type { Instrument, Order } from "./schema";
import { orderSchema, portfolioSchema, previewSchema } from "./schema";
import { TradePreview } from "./TradePreview";
import { ErrorNotice, Modal } from "./ui";
import { useOnline } from "./useOnline";

type Side = "buy" | "sell";
type Draft = {
	readonly quantity: string;
	readonly price: string;
	readonly type: "limit" | "market";
	readonly reason: string;
	readonly target: string;
	readonly stop: string;
	readonly requestId: string;
};
export function Trade({
	instrument,
	side,
	onClose,
	onSuccess,
	original,
	draftRoundId,
}: {
	readonly instrument: Instrument;
	readonly side: Side;
	readonly original?: Order;
	readonly draftRoundId?: string;
	readonly onClose: () => void;
	readonly onSuccess: () => void;
}) {
	const online = useOnline();
	const [draft, setDraft] = useState<Draft>({
		quantity: original
			? String(original.quantity)
			: String(
					side === "buy"
						? (instrument.min_buy_quantity ?? instrument.lot_size)
						: (instrument.quantity_step ?? instrument.lot_size),
				),
		price: original?.limit_price ?? "",
		type: original?.order_type ?? "limit",
		reason: original?.reason ?? "",
		target: original?.target_price ?? "",
		stop: original?.stop_price ?? "",
		requestId: crypto.randomUUID(),
	});
	const client = useQueryClient();
	const [selectedPlan, setSelectedPlan] = useState<Plan | null>(null);
	const [planBlocked, setPlanBlocked] = useState(Boolean(original?.plan_id));
	const [planNotice, setPlanNotice] = useState(
		original?.plan_id ? "正在核对原关联计划…" : "",
	);
	const attempted = useRef(new Set<string>());
	const actionLock = useRef(false);
	const assertRound = async () => {
		if (
			draftRoundId &&
			(await api("portfolio", portfolioSchema)).round_id !== draftRoundId
		)
			throw new ApiError(
				"round_changed",
				"练习轮次已变化，草稿已保留。请关闭后在当前轮重新建立委托。",
				409,
			);
	};
	const loadOriginalPlan = useMutation({
		mutationFn: async () => {
			await assertRound();
			const plan = await api(
				`plans/${encodeURIComponent(original?.plan_id ?? "")}`,
				planSchema,
			);
			if (plan.round_id !== draftRoundId)
				throw new Error("原关联计划已归档，不能关联新委托。");
			if (plan.status === "ended")
				throw new Error("原关联计划已结束，不能关联新委托。");
			return plan;
		},
		onSuccess: (plan) => {
			setSelectedPlan(plan);
			setPlanBlocked(false);
			setPlanNotice(
				plan.version !== original?.plan_version
					? `原计划已由版本 ${original?.plan_version ?? "未知"} 更新至 ${plan.version}；关联当前版本，保留原委托理由与参考价。`
					: "已核对原关联计划，保留原委托理由与参考价。",
			);
			change({});
		},
		onError: (error) => {
			setPlanBlocked(true);
			setPlanNotice(
				`无法关联原计划：${errorMessage(error)} 请重试，或明确选择不关联计划。`,
			);
		},
	});
	useEffect(() => {
		if (original?.plan_id) loadOriginalPlan.mutate();
	}, [original?.plan_id, loadOriginalPlan.mutate]);
	const payload = {
		request_id: draft.requestId,
		...(draftRoundId ? { expected_round_id: draftRoundId } : {}),
		...(selectedPlan
			? { plan_id: selectedPlan.id, plan_version: selectedPlan.version }
			: {}),
		symbol: instrument.symbol,
		side,
		order_type: draft.type,
		quantity: Number(draft.quantity),
		...(draft.type === "limit" ? { limit_price: draft.price } : {}),
		reason: draft.reason,
		...(draft.target ? { target_price: draft.target } : {}),
		...(draft.stop ? { stop_price: draft.stop } : {}),
	};
	const [unresolved, setUnresolved] = useState<typeof payload | null>(null);
	const preview = useMutation({
		onSettled: () => {
			actionLock.current = false;
		},
		mutationFn: async () => {
			await assertRound();
			return api("orders/preview", previewSchema, payload);
		},
	});
	const sizing = useMutation({
		mutationFn: (percent: number) =>
			api(
				`orders/size?percent=${percent}`,
				z.object({
					max_quantity: z.number().int(),
					suggested_quantity: z.number().int(),
					quantity_step: z.number().int(),
					minimum_quantity: z.number().int(),
				}),
				{ ...payload, quantity: 1 },
			),
		onSuccess: (result) => {
			change({ quantity: String(result.suggested_quantity) });
		},
	});
	const submit = useMutation({
		onSettled: () => {
			actionLock.current = false;
		},
		mutationFn: async (original: typeof payload) => {
			if (!attempted.current.has(original.request_id)) await assertRound();
			attempted.current.add(original.request_id);
			return api("orders", orderSchema, original);
		},
		onError: (error, original) => {
			if (
				error instanceof ApiError &&
				error.status !== undefined &&
				error.status >= 400 &&
				error.status < 500 &&
				error.status !== 408
			) {
				attempted.current.delete(original.request_id);
				setUnresolved(null);
				preview.reset();
			}
		},
		onSuccess: async () => {
			await Promise.all([
				client.invalidateQueries({ queryKey: ["portfolio"] }),
				client.invalidateQueries({ queryKey: ["orders"] }),
			]);
			onSuccess();
		},
	});
	useEffect(() => {
		if (!online) preview.reset();
	}, [online, preview.reset]);
	useEffect(() => {
		const resume = () => {
			if (document.visibilityState === "visible") preview.reset();
		};
		document.addEventListener("visibilitychange", resume);
		return () => document.removeEventListener("visibilitychange", resume);
	}, [preview.reset]);
	const change = (patch: Partial<Draft>) => {
		setDraft((previous) => ({
			...previous,
			...patch,
			requestId: crypto.randomUUID(),
		}));
		preview.reset();
		submit.reset();
	};
	if (
		!instrument.tradable ||
		instrument.lot_size === null ||
		instrument.tick_size === null
	) {
		return (
			<Modal title={instrument.name} onClose={onClose}>
				<p className="notice">
					该证券仅供观察，交易规则核验完成前不能提交模拟委托。
				</p>
			</Modal>
		);
	}
	return (
		<Modal
			title={`${side === "buy" ? "模拟买入" : "模拟卖出"} · ${instrument.name}`}
			onClose={onClose}
		>
			{original && (
				<p className="notice">
					已按原参数建立新草稿，原委托和历史计划不变。请重新预览并确认提交。
				</p>
			)}
			<form
				onSubmit={(e) => {
					e.preventDefault();
					if (
						!online ||
						actionLock.current ||
						planBlocked ||
						loadOriginalPlan.isPending ||
						sizing.isPending ||
						preview.isPending ||
						submit.isPending
					)
						return;
					actionLock.current = true;
					if (unresolved) submit.mutate(unresolved);
					else if (preview.isSuccess) {
						setUnresolved(payload);
						submit.mutate(payload);
					} else preview.mutate();
				}}
			>
				<fieldset
					disabled={
						!!unresolved ||
						loadOriginalPlan.isPending ||
						!online ||
						sizing.isPending ||
						preview.isPending ||
						submit.isPending
					}
				>
					{original?.plan_id && (
						<div className="notice" role="status">
							<p>{planNotice}</p>
							<div className="button-row">
								<button
									type="button"
									className="button small"
									disabled={loadOriginalPlan.isPending}
									onClick={() => {
										setPlanBlocked(true);
										loadOriginalPlan.mutate();
									}}
								>
									重新核对原计划
								</button>
								<button
									type="button"
									className="button small"
									disabled={loadOriginalPlan.isPending}
									onClick={() => {
										setSelectedPlan(null);
										setPlanBlocked(false);
										setPlanNotice(
											"已明确选择不关联计划；保留原委托理由与参考价。",
										);
										change({});
									}}
								>
									明确不关联计划
								</button>
							</div>
						</div>
					)}
					{!original?.plan_id && planBlocked && (
						<p className="notice" role="status">
							{planNotice}
						</p>
					)}
					<PlanPicker
						preserveDraft={Boolean(original)}
						onReload={(plan) => {
							setPlanBlocked(false);
							setPlanNotice("已重新核对所选计划，保留原委托理由与参考价。");
							setSelectedPlan(plan);
							change({});
						}}
						onInvalid={(message) => {
							setPlanBlocked(true);
							setPlanNotice(message);
						}}
						symbol={instrument.symbol}
						selected={selectedPlan}
						onSelect={(plan) => {
							setSelectedPlan(plan);
							setPlanNotice(
								plan
									? "已明确选择计划，带入该计划的理由与参考价。"
									: "已明确选择不关联计划，保留交易理由与参考价。",
							);
							setPlanBlocked(false);
							change(
								plan
									? {
											reason: plan.reason,
											target: plan.target_price ?? "",
											stop: plan.stop_price ?? "",
										}
									: {},
							);
						}}
					/>
					<div className="field">
						<label htmlFor="order-type">委托方式</label>
						<select
							id="order-type"
							value={draft.type}
							onChange={(e) =>
								change({
									type: e.target.value === "market" ? "market" : "limit",
								})
							}
						>
							<option value="limit">限价单</option>
							<option value="market">市价模拟 · 2% 价格保护</option>
						</select>
					</div>
					<div className="form-grid">
						<div className="field">
							<label htmlFor="quantity">
								{side === "buy" &&
								instrument.min_buy_quantity != null &&
								instrument.min_buy_quantity !== instrument.quantity_step
									? `数量（${instrument.min_buy_quantity} 起，递增 ${instrument.quantity_step ?? instrument.lot_size}）`
									: `数量（每手 ${instrument.lot_size}）`}
							</label>
							<input
								id="quantity"
								inputMode="numeric"
								type="number"
								min={
									side === "buy"
										? (instrument.min_buy_quantity ?? instrument.lot_size)
										: 1
								}
								step={
									side === "buy"
										? (instrument.quantity_step ?? instrument.lot_size)
										: 1
								}
								required
								value={draft.quantity}
								onChange={(e) => change({ quantity: e.target.value })}
							/>
						</div>
						{draft.type === "limit" && (
							<div className="field">
								<label htmlFor="limit">限价（{instrument.currency}）</label>
								<input
									id="limit"
									inputMode="decimal"
									type="number"
									min={instrument.tick_size}
									step={
										instrument.tick_rule === "HK" ||
										instrument.tick_rule === "US"
											? "any"
											: instrument.tick_size
									}
									required
									value={draft.price}
									onChange={(e) => change({ price: e.target.value })}
								/>
							</div>
						)}
					</div>
					<p className="footnote">
						{instrument.market} · 买入{" "}
						{instrument.min_buy_quantity ?? instrument.lot_size} 股 / 份起，递增{" "}
						{instrument.quantity_step ?? instrument.lot_size} 股 /
						份。卖出数量以可卖持仓和预览校验为准。{" "}
						{instrument.tick_rule === "HK"
							? "港股价位随价格区间与证券类型变化，以预览校验为准。"
							: instrument.tick_rule === "US"
								? "美股低于 1 美元的报价可保留四位小数，以预览校验为准。"
								: `最小价位 ${instrument.tick_size} ${instrument.currency}`}{" "}
						数量快捷选择由账户可用资金或可卖份额计算，含费用与换汇，仍需预览。
					</p>
					<div className="button-row">
						{[25, 50, 100].map((percent) => (
							<button
								key={percent}
								type="button"
								className="button small"
								disabled={
									sizing.isPending ||
									!online ||
									(draft.type === "limit" && !draft.price)
								}
								onClick={() => sizing.mutate(percent)}
							>
								{percent}%
							</button>
						))}
					</div>
					{sizing.isError && (
						<ErrorNotice message={errorMessage(sizing.error)} />
					)}
					<div className="field">
						<label htmlFor="reason">
							这次交易的理由 <span>选填</span>
						</label>
						<textarea
							id="reason"
							maxLength={2000}
							placeholder="你看到了什么，什么情况会改变你的判断？"
							value={draft.reason}
							onChange={(e) => change({ reason: e.target.value })}
						/>
					</div>
					<details>
						<summary>记录目标价与止损参考价</summary>
						<p className="footnote">
							以原币记录参考值，可保留四位小数；不会自动触发买卖。
						</p>
						<div className="form-grid">
							<div className="field">
								<label htmlFor="target">目标价</label>
								<input
									id="target"
									type="number"
									inputMode="decimal"
									min="0.0001"
									step="any"
									value={draft.target}
									onChange={(e) => change({ target: e.target.value })}
								/>
							</div>
							<div className="field">
								<label htmlFor="stop">止损参考价</label>
								<input
									id="stop"
									type="number"
									inputMode="decimal"
									min="0.0001"
									step="any"
									value={draft.stop}
									onChange={(e) => change({ stop: e.target.value })}
								/>
							</div>
						</div>
					</details>
				</fieldset>
				{preview.isError && (
					<ErrorNotice message={errorMessage(preview.error)} />
				)}{" "}
				{submit.isError && <ErrorNotice message={errorMessage(submit.error)} />}
				{unresolved && !submit.isPending && (
					<p className="notice" role="status">
						委托结果尚未确认。请联网后重试确认结果；将核对原委托，不会重复创建。确认前暂不能修改交易内容。
					</p>
				)}
				{preview.isSuccess && (
					<TradePreview
						preview={preview.data}
						currency={instrument.currency}
						side={side}
					/>
				)}
				<p className="footnote">
					提交后等待有效行情确认，可能延迟成交。撤单确认前，资金或持仓继续占用。
				</p>
				<button
					className="button primary full"
					type="submit"
					disabled={
						!online ||
						planBlocked ||
						loadOriginalPlan.isPending ||
						sizing.isPending ||
						preview.isPending ||
						submit.isPending
					}
				>
					{submit.isPending
						? "正在提交…"
						: unresolved
							? "重试确认委托结果"
							: preview.isPending
								? "正在计算…"
								: preview.isSuccess
									? "确认提交委托"
									: "预览费用与资金占用"}
				</button>
			</form>
		</Modal>
	);
}
