import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api, errorMessage, money, price, time } from "./api";
import { orderSchema, previewSchema } from "./schema";
import type { Instrument } from "./schema";
import { ErrorNotice, Modal } from "./ui";
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
}: {
	readonly instrument: Instrument;
	readonly side: Side;
	readonly onClose: () => void;
	readonly onSuccess: () => void;
}) {
	const [draft, setDraft] = useState<Draft>({
		quantity: String(instrument.lot_size),
		price: "",
		type: "limit",
		reason: "",
		target: "",
		stop: "",
		requestId: crypto.randomUUID(),
	});
	const client = useQueryClient();
	const payload = {
		request_id: draft.requestId,
		symbol: instrument.symbol,
		side,
		order_type: draft.type,
		quantity: Number(draft.quantity),
		...(draft.type === "limit" ? { limit_price: draft.price } : {}),
		reason: draft.reason,
		...(draft.target ? { target_price: draft.target } : {}),
		...(draft.stop ? { stop_price: draft.stop } : {}),
	};
	const preview = useMutation({
		mutationFn: () => api("orders/preview", previewSchema, payload),
	});
	const submit = useMutation({
		mutationFn: () => api("orders", orderSchema, payload),
		onSuccess: async () => {
			await Promise.all([
				client.invalidateQueries({ queryKey: ["portfolio"] }),
				client.invalidateQueries({ queryKey: ["orders"] }),
			]);
			onSuccess();
		},
	});
	const change = (patch: Partial<Draft>) => {
		setDraft((previous) => ({
			...previous,
			...patch,
			requestId: crypto.randomUUID(),
		}));
		preview.reset();
		submit.reset();
	};
	return (
		<Modal
			title={`${side === "buy" ? "模拟买入" : "模拟卖出"} · ${instrument.name}`}
			onClose={onClose}
		>
			<form
				onSubmit={(e) => {
					e.preventDefault();
					if (preview.isSuccess) submit.mutate();
					else preview.mutate();
				}}
			>
				<fieldset disabled={preview.isPending || submit.isPending}>
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
								数量（每手 {instrument.lot_size}）
							</label>
							<input
								id="quantity"
								inputMode="numeric"
								type="number"
								min={instrument.lot_size}
								step={instrument.lot_size}
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
									step={instrument.tick_size}
									required
									value={draft.price}
									onChange={(e) => change({ price: e.target.value })}
								/>
							</div>
						)}
					</div>
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
						<p className="footnote">仅供复盘，不会自动触发买卖。</p>
						<div className="form-grid">
							<div className="field">
								<label htmlFor="target">目标价</label>
								<input
									id="target"
									type="number"
									min={instrument.tick_size}
									step={instrument.tick_size}
									value={draft.target}
									onChange={(e) => change({ target: e.target.value })}
								/>
							</div>
							<div className="field">
								<label htmlFor="stop">止损参考价</label>
								<input
									id="stop"
									type="number"
									min={instrument.tick_size}
									step={instrument.tick_size}
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
				{preview.isSuccess && (
					<div className="preview-box">
						<h3>确认这笔委托</h3>
						<dl className="summary-list">
							<div>
								<dt>价格边界</dt>
								<dd>
									{price(
										preview.data.price_bound,
										instrument.currency,
										instrument.tick_size,
									)}
								</dd>
							</div>
							<div>
								<dt>预计费用</dt>
								<dd>
									{money(preview.data.estimated_fee, instrument.currency)}
								</dd>
							</div>
							<div>
								<dt>占用人民币</dt>
								<dd>{money(preview.data.reserved_cny)}</dd>
							</div>
							<div>
								<dt>占用外币</dt>
								<dd>
									{money(preview.data.reserved_foreign, instrument.currency)}
								</dd>
							</div>
							{preview.data.fx_rate && (
								<div>
									<dt>换汇参考</dt>
									<dd>
										1 {instrument.currency} = {preview.data.fx_rate} CNY
									</dd>
								</div>
							)}
							<div>
								<dt>有效至（北京时间）</dt>
								<dd>{time(preview.data.expires_at)}</dd>
							</div>
						</dl>
						{preview.data.warnings.map((w) => (
							<p key={w} className="notice">
								{w}
							</p>
						))}
					</div>
				)}
				<p className="footnote">
					提交后等待有效行情确认，可能延迟成交。撤单确认前，资金或持仓继续占用。
				</p>
				<button
					className="button primary full"
					type="submit"
					disabled={preview.isPending || submit.isPending}
				>
					{submit.isPending
						? "正在提交…"
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
