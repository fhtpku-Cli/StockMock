import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
	useEffect,
	useLayoutEffect,
	useRef,
	useState,
	useSyncExternalStore,
} from "react";
import { z } from "zod";
import {
	ApiError,
	api,
	errorMessage,
	fullTime,
	money,
	price,
	SESSION_EXPIRED,
} from "./api";
import { PlanPicker } from "./PlanPicker";
import {
	clearPendingOrder,
	invalidateOrderPlanQueries,
	isFinalOrderRejection,
	type Submission,
	savePendingOrder,
} from "./pendingOrder";
import { type Plan, planPageSchema, planSchema } from "./planSchema";
import type { Instrument, Order } from "./schema";
import { orderSchema, portfolioSchema, previewSchema } from "./schema";
import "./styles/trade-reference.css";
import { TradePreview } from "./TradePreview";
import { ErrorNotice, Modal } from "./ui";
import { useOnline } from "./useOnline";
import { useTradeReference } from "./useTradeReference";

type Side = "buy" | "sell";
type Draft = {
	readonly quantity: string;
	readonly price: string;
	readonly type: "limit" | "market";
	readonly reason: string;
	readonly target: string;
	readonly stop: string;
	readonly saveAsPlan: boolean;
	readonly reviewDate: string;
	readonly requestId: string;
};
function useFirstPlan(
	symbol: string,
	status: "active" | "draft",
	enabled: boolean,
) {
	return useQuery({
		queryKey: ["plans", "order-option", symbol, status],
		queryFn: () =>
			api(
				`plans?symbol=${encodeURIComponent(symbol)}&status=${status}&limit=1`,
				planPageSchema,
			),
		enabled,
	});
}

function hasPlanBasis(draft: Draft) {
	return Boolean(draft.reason.trim() || draft.target || draft.stop);
}

const SYNC_CONFLICT =
	"该证券刚有进行中计划。可带入该计划，或取消同步后重新预览。";
// 首次提交与预览返回 plan_sync_conflict；带重试头时同一拒绝以 order_receipt_rejected 返回。
function isSyncConflict(error: unknown) {
	return (
		error instanceof ApiError &&
		(error.code === "plan_sync_conflict" ||
			(error.code === "order_receipt_rejected" &&
				error.message.includes(SYNC_CONFLICT)))
	);
}

// 与 mobile-surfaces.css 的窄屏/横屏触屏断点一致；桌面宽度保持原布局。
const COMPACT = "(max-width: 700px), (max-height: 500px) and (pointer: coarse)";
function watchCompact(notify: () => void) {
	const query = window.matchMedia(COMPACT);
	query.addEventListener("change", notify);
	return () => query.removeEventListener("change", notify);
}
function useCompact() {
	return useSyncExternalStore(
		watchCompact,
		() => window.matchMedia(COMPACT).matches,
		() => false,
	);
}

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
	readonly onSuccess: (order: Order) => void;
}) {
	const online = useOnline();
	const { portfolio, quote } = useTradeReference(instrument.symbol);
	const cash = portfolio.data?.cash.find(
		(row) => row.currency === instrument.currency,
	);
	const position = portfolio.data?.positions.find(
		(row) => row.symbol === instrument.symbol,
	);
	const previewStart = useRef<HTMLElement>(null);
	const actions = useRef<HTMLDivElement>(null);
	const compact = useCompact();
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
		saveAsPlan: false,
		reviewDate: "",
		requestId: crypto.randomUUID(),
	});
	const client = useQueryClient();
	const [selectedPlan, setSelectedPlan] = useState<Plan | null>(null);
	const [planBlocked, setPlanBlocked] = useState(Boolean(original?.plan_id));
	const [planNotice, setPlanNotice] = useState(
		original?.plan_id ? "正在核对原关联计划…" : "",
	);
	const checkPlans =
		side === "buy" &&
		instrument.tradable &&
		online &&
		!selectedPlan &&
		!planBlocked;
	const activePlan = useFirstPlan(instrument.symbol, "active", checkPlans);
	const draftPlan = useFirstPlan(instrument.symbol, "draft", checkPlans);
	const [openedAt] = useState(() => Date.now());
	const planTaken = activePlan.isSuccess && activePlan.data.total > 0;
	// 打开窗口前的缓存要等本次查询完成；窗口内取得的结果在后台重新获取时沿用，
	// 冲突由预览检查和下单时的唯一索引兜底。
	const canSync =
		checkPlans &&
		hasPlanBasis(draft) &&
		activePlan.isSuccess &&
		(!activePlan.isFetching || activePlan.dataUpdatedAt >= openedAt) &&
		activePlan.data.total === 0;
	const today = fullTime(new Date().toISOString()).slice(0, 10);
	const attempted = useRef(new Set<string>());
	const retried = useRef(new Set<string>());
	const previewRound = useRef("");
	const actionLock = useRef(false);
	const mounted = useRef(true);
	useEffect(() => {
		mounted.current = true;
		const expire = () => {
			mounted.current = false;
		};
		window.addEventListener(SESSION_EXPIRED, expire);
		return () => {
			mounted.current = false;
			window.removeEventListener(SESSION_EXPIRED, expire);
		};
	}, []);
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
			change({ saveAsPlan: false });
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
		...(draft.saveAsPlan
			? {
					save_as_plan: true,
					...(draft.reviewDate ? { plan_review_date: draft.reviewDate } : {}),
				}
			: {}),
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
	const [unresolved, setUnresolved] = useState<Submission | null>(null);
	const [validityNotice, setValidityNotice] = useState("");
	const preview = useMutation({
		onError: (error) => {
			if (error instanceof ApiError && error.code === "plan_sync_conflict") {
				setDraft((prior) => ({ ...prior, requestId: crypto.randomUUID() }));
				void client.invalidateQueries({ queryKey: ["plans"] });
			}
		},
		onSettled: () => {
			actionLock.current = false;
		},
		mutationFn: async () => {
			setValidityNotice("");
			previewRound.current = (await api("portfolio", portfolioSchema)).round_id;
			if (draftRoundId && previewRound.current !== draftRoundId)
				throw new ApiError(
					"round_changed",
					"练习轮次已变化，请关闭后重新建立委托。",
					409,
				);
			return api("orders/preview", previewSchema, {
				...payload,
				expected_round_id: draftRoundId ?? previewRound.current,
			});
		},
	});
	useEffect(() => {
		if (!preview.isSuccess) return;
		previewStart.current?.focus({ preventScroll: true });
		previewStart.current?.scrollIntoView({ block: "start" });
	}, [preview.isSuccess]);
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
		mutationFn: async (original: Submission) => {
			const retry = attempted.current.has(original.request_id);
			if (!retry) {
				await assertRound();
				if (!mounted.current)
					throw new ApiError("session_changed", "请重新登录后核对委托。");
				savePendingOrder(original, instrument.currency);
				setUnresolved(original);
			}
			attempted.current.add(original.request_id);
			if (retry) retried.current.add(original.request_id);
			return api(
				"orders",
				orderSchema,
				original,
				retry ? { headers: { "X-StockMock-Retry": "1" } } : undefined,
			);
		},
		onError: async (error, original) => {
			if (!mounted.current) return;
			const finalRejection = isFinalOrderRejection(
				error,
				retried.current.has(original.request_id),
			);
			if (finalRejection) {
				try {
					clearPendingOrder(original.request_id);
				} catch (storageError) {
					setValidityNotice(errorMessage(storageError));
					return;
				}
				attempted.current.delete(original.request_id);
				retried.current.delete(original.request_id);
				setUnresolved(null);
				setDraft((previous) => ({
					...previous,
					requestId: crypto.randomUUID(),
				}));
				preview.reset();
				if (
					original.save_as_plan &&
					error instanceof ApiError &&
					["plan_sync_conflict", "order_receipt_rejected"].includes(error.code)
				)
					void client.invalidateQueries({ queryKey: ["plans"] });
			}
			if (
				finalRejection &&
				error instanceof ApiError &&
				error.code === "validity_changed"
			) {
				const previous = `原预览有效至 ${fullTime(original.expected_expires_at)}（北京时间）`;
				setValidityNotice(`${previous}；正在核对新的有效期。草稿已保留。`);
				try {
					const current = await api("orders/preview", previewSchema, original);
					if (!mounted.current) return;
					setValidityNotice(
						`${previous}；当前有效至 ${fullTime(current.expires_at)}（北京时间）。本次提交未受理，请重新预览后确认。`,
					);
				} catch (previewError) {
					if (!mounted.current) return;
					if (!(previewError instanceof Error)) throw previewError;
					setValidityNotice(
						`${previous}；新有效期暂无法取得，请重新预览。草稿已保留。`,
					);
				}
			}
		},
		onSuccess: (order, original) => {
			if (!mounted.current) return;
			if (original.save_as_plan) void invalidateOrderPlanQueries(client);
			void Promise.all([
				client.invalidateQueries({ queryKey: ["portfolio"] }),
				client.invalidateQueries({ queryKey: ["orders"] }),
			]);
			onSuccess(order);
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
	const stickyActions = compact && (preview.isSuccess || !!unresolved);
	useLayoutEffect(() => {
		const bar = actions.current;
		const dialog = bar?.closest("dialog");
		if (!stickyActions || !bar || !dialog) return;
		const measure = () => {
			dialog.style.scrollPaddingBottom = `${bar.offsetHeight}px`;
		};
		measure();
		const observer = new ResizeObserver(measure);
		observer.observe(bar);
		return () => {
			observer.disconnect();
			dialog.style.removeProperty("scroll-padding-bottom");
		};
	}, [stickyActions]);
	const conflictError =
		isSyncConflict(preview.error) || isSyncConflict(submit.error);
	const syncBlocked =
		draft.saveAsPlan && !unresolved && (!canSync || conflictError);
	const syncConflict = syncBlocked && (planTaken || conflictError);
	const shownError = (error: Error | null) =>
		error && !(syncConflict && isSyncConflict(error)) ? error : null;
	const previewError = shownError(preview.error);
	const submitError = shownError(submit.error);
	const change = (patch: Partial<Draft>) => {
		setDraft((previous) => {
			const next = { ...previous, ...patch, requestId: crypto.randomUUID() };
			if (!next.saveAsPlan || !hasPlanBasis(next))
				return { ...next, saveAsPlan: false, reviewDate: "" };
			return next;
		});
		preview.reset();
		submit.reset();
		setValidityNotice("");
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
	const submitNotices = (
		<>
			{submitError && <ErrorNotice message={errorMessage(submitError)} />}
			{validityNotice && (
				<p className="notice" role="status">
					{validityNotice}
				</p>
			)}
			{unresolved && !submit.isPending && (
				<p className="notice" role="status">
					委托结果尚未确认。请联网后重试确认结果；将核对原委托，不会重复创建。确认前暂不能修改交易内容或关闭窗口。
				</p>
			)}
		</>
	);
	return (
		<Modal
			title={`${side === "buy" ? "模拟买入" : "模拟卖出"} · ${instrument.name}`}
			onClose={onClose}
			closeDisabled={!!unresolved || submit.isPending}
		>
			{!original && (
				<button
					type="button"
					className="text-button"
					disabled={!!unresolved || submit.isPending}
					onClick={onClose}
				>
					返回标的详情
				</button>
			)}
			<div className="trade-reference">
				<p>
					参考报价 ·{" "}
					{quote.isSuccess && online
						? `${price(quote.data.price, quote.data.currency, quote.data.tick_size ?? instrument.tick_size ?? undefined)} · ${fullTime(quote.data.market_time)}（北京时间） · ${quote.data.source}`
						: quote.isPending && online
							? "读取中…"
							: "暂不可用"}
				</p>
				{quote.isSuccess && online && quote.data.message && (
					<p>{quote.data.message}</p>
				)}
				<p>
					{side === "buy"
						? `${instrument.currency} 可用现金 · ${portfolio.isSuccess && online && cash ? money(cash.available, cash.currency) : "暂不可用"}`
						: `当前可卖 · ${portfolio.isSuccess && online ? `${position?.sellable ?? 0} 股 / 份` : "暂不可用"}`}
					{side === "buy" &&
						instrument.currency !== "CNY" &&
						" · 换汇金额见预览"}
				</p>
			</div>
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
						(!unresolved && draft.saveAsPlan && !canSync) ||
						loadOriginalPlan.isPending ||
						sizing.isPending ||
						preview.isPending ||
						submit.isPending
					)
						return;
					actionLock.current = true;
					if (unresolved) submit.mutate(unresolved);
					else if (preview.isSuccess) {
						const submission = {
							...payload,
							expected_round_id: draftRoundId ?? previewRound.current,
							expected_expires_at: preview.data.expires_at,
						};
						submit.mutate(submission);
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
										change({ saveAsPlan: false });
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
							change({ saveAsPlan: false });
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
											saveAsPlan: false,
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
					{checkPlans &&
						!planTaken &&
						draftPlan.isSuccess &&
						draftPlan.data.total > 0 && (
							<p className="footnote">
								已有草稿计划；关联草稿不会产生提醒，如需提醒请先在计划中启用。
							</p>
						)}
					{canSync && (
						<label className="confirm-check">
							<input
								type="checkbox"
								checked={draft.saveAsPlan}
								onChange={(event) =>
									change({ saveAsPlan: event.target.checked })
								}
							/>
							同时保存为该证券的进行中计划（启用到价提醒与复盘提醒）
						</label>
					)}
					{checkPlans && planTaken && !draft.saveAsPlan && (
						<div className="field">
							<p className="footnote">
								该证券已有进行中计划，可在「带入已有计划」中选择，沿用其提醒。
							</p>
						</div>
					)}
					{syncBlocked && (
						<div className="notice sync-notice" role="status">
							<p>
								{syncConflict
									? SYNC_CONFLICT
									: "暂未确认是否有进行中计划。你可以等查询完成，或取消同步后重新预览。"}
							</p>
							<div className="button-row">
								<button
									type="button"
									className="button small"
									onClick={() => change({ saveAsPlan: false })}
								>
									取消同步计划
								</button>
							</div>
						</div>
					)}
					{canSync && draft.saveAsPlan && (
						<div className="field">
							<label htmlFor="plan-review-date">
								计划复盘日期（北京时间，选填）
							</label>
							<input
								id="plan-review-date"
								type="date"
								min={today}
								value={draft.reviewDate}
								onChange={(event) => change({ reviewDate: event.target.value })}
							/>
							{draft.reviewDate === today && (
								<p className="footnote">
									计划复盘日期是今天，提交后通常 1–2 分钟内会出现在站内提醒中
								</p>
							)}
						</div>
					)}
				</fieldset>
				{previewError && <ErrorNotice message={errorMessage(previewError)} />}{" "}
				{!stickyActions && submitNotices}
				{preview.isSuccess && (
					<section
						ref={previewStart}
						tabIndex={-1}
						aria-label="委托预览"
						className="trade-preview-start"
					>
						<TradePreview
							orderType={draft.type}
							preview={preview.data}
							currency={instrument.currency}
							side={side}
							quantity={payload.quantity}
						/>
					</section>
				)}
				<p className="footnote">
					提交后等待有效行情确认，可能延迟成交。撤单确认前，资金或持仓继续占用。
				</p>
				<div
					ref={actions}
					className={stickyActions ? "trade-actions" : undefined}
				>
					{stickyActions && submitNotices}
					<button
						className="button primary full"
						type="submit"
						disabled={
							!online ||
							planBlocked ||
							(!unresolved && draft.saveAsPlan && !canSync) ||
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
				</div>
			</form>
		</Modal>
	);
}
