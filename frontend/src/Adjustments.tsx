import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { z } from "zod";
import { adjustmentSchema } from "./adjustmentSchema";
import { api, errorMessage, money, time } from "./api";
import { portfolioSchema } from "./schema";
import { ErrorNotice, Loading, Modal, Panel } from "./ui";
import { useOnline } from "./useOnline";
export function AdjustmentRecords({ roundId }: { readonly roundId?: string }) {
	const query = useQuery({
		queryKey: ["adjustments", roundId ?? "current"],
		queryFn: () =>
			api(
				roundId
					? `rounds/${encodeURIComponent(roundId)}/adjustments`
					: "adjustments",
				z.array(adjustmentSchema),
			),
	});
	const [payment, setPayment] = useState<{
		readonly id: string;
		readonly round: string;
		readonly request: string;
	} | null>(null);
	const online = useOnline();
	const client = useQueryClient();
	const pay = useMutation({
		mutationFn: () => {
			if (!payment) throw new Error("尚未选择应收记录");
			return api(`adjustments/${payment.id}/pay`, adjustmentSchema, {
				request_id: payment.request,
				round_id: payment.round,
			});
		},
		onSuccess: async () => {
			setPayment(null);
			await client.invalidateQueries();
		},
	});
	return (
		<Panel title="分红与拆股记录">
			{query.isPending ? (
				<Loading />
			) : query.isError ? (
				<ErrorNotice
					message={errorMessage(query.error)}
					retry={() => void query.refetch()}
				/>
			) : query.data.length === 0 ? (
				<p className="muted">暂无人工调整记录。</p>
			) : (
				query.data.map((a) => (
					<article className="order-row" key={a.id}>
						<strong>
							{a.symbol} · {a.kind === "dividend" ? "现金分红" : "拆股 / 合股"}
						</strong>
						<p className="footnote">
							生效 {a.effective_date} · 登记 {time(a.created_at)}
						</p>
						<p>{a.note}</p>
						<dl className="summary-list">
							<div>
								<dt>份额变化</dt>
								<dd>
									{a.quantity_before} → {a.quantity_after}
								</dd>
							</div>
							<div>
								<dt>现金变动</dt>
								<dd>{money(a.cash_delta, a.currency)}</dd>
							</div>
							<div>
								<dt>总成本变化</dt>
								<dd>
									{money(a.cost_before, a.currency)} →{" "}
									{money(a.cost_after, a.currency)}
								</dd>
							</div>
							{a.kind === "dividend" && (
								<div>
									<dt>{a.paid_at ? "已收款" : "待收款"}</dt>
									<dd>
										{money(
											a.paid_at ? a.cash_delta : a.receivable_amount,
											a.currency,
										)}{" "}
										{a.paid_at ? time(a.paid_at) : ""}
									</dd>
								</div>
							)}
						</dl>
						{!roundId && a.kind === "dividend" && !a.paid_at && (
							<button
								type="button"
								className="button"
								disabled={!online}
								onClick={() => {
									pay.reset();
									setPayment({
										id: a.id,
										round: a.round_id,
										request: crypto.randomUUID(),
									});
								}}
							>
								登记收到股息
							</button>
						)}
					</article>
				))
			)}
			{payment && (
				<Modal title="确认收到股息" onClose={() => setPayment(null)}>
					<p>
						请核对股息已到账。确认后增加对应币种现金并留下不可删除的流水，不会重复入账。
					</p>
					{pay.isError && <ErrorNotice message={errorMessage(pay.error)} />}
					<button
						type="button"
						className="button primary"
						disabled={!online || pay.isPending}
						onClick={() => pay.mutate()}
					>
						确认收款并记账
					</button>
				</Modal>
			)}
		</Panel>
	);
}
export function Adjustments({ onClose }: { readonly onClose: () => void }) {
	const p = useQuery({
		queryKey: ["portfolio"],
		queryFn: () => api("portfolio", portfolioSchema),
	});
	const [symbol, setSymbol] = useState("");
	const [kind, setKind] = useState("dividend");
	const [date, setDate] = useState("");
	const [note, setNote] = useState("");
	const [cash, setCash] = useState("");
	const [payment, setPayment] = useState("paid");
	const [numerator, setNumerator] = useState("2");
	const [denominator, setDenominator] = useState("1");
	const [confirmed, setConfirmed] = useState<{
		readonly quantity: number;
		readonly round: string;
	} | null>(null);
	const [requestId, setRequestId] = useState(() => crypto.randomUUID());
	const online = useOnline(),
		client = useQueryClient();
	const position = p.data?.positions.find((row) => row.symbol === symbol);
	const save = useMutation({
		mutationFn: () =>
			api("adjustments", adjustmentSchema, {
				request_id: requestId,
				round_id: confirmed?.round,
				symbol,
				kind,
				effective_date: date,
				expected_quantity: confirmed?.quantity,
				note,
				...(kind === "dividend"
					? { cash_per_share: cash, payment_status: payment }
					: { numerator: Number(numerator), denominator: Number(denominator) }),
			}),
		onSuccess: async () => {
			setConfirmed(null);
			setRequestId(crypto.randomUUID());
			await client.invalidateQueries();
		},
	});
	const changed = () => {
		setConfirmed(null);
		setRequestId(crypto.randomUUID());
		save.reset();
	};
	return (
		<Modal title="人工登记分红与拆股" onClose={onClose} wide>
			<p className="notice">
				仅登记你已核对的公司行动，不自动获取公告。请先结束该标的未完成委托；记录入账后不能删除或改写。拆股后须为整数份额。
			</p>
			{p.isPending ? (
				<Loading />
			) : p.isError ? (
				<ErrorNotice message={errorMessage(p.error)} />
			) : p.data.positions.length === 0 ? (
				<p className="muted">当前没有持仓，暂不能登记。</p>
			) : (
				<form
					onSubmit={(e) => {
						e.preventDefault();
						if (confirmed && online) save.mutate();
					}}
					onChange={changed}
				>
					<fieldset disabled={save.isPending || !online}>
						<div className="form-grid">
							<div className="field">
								<label htmlFor="adjust-symbol">当前持仓</label>
								<select
									id="adjust-symbol"
									required
									value={symbol}
									onChange={(e) => setSymbol(e.target.value)}
								>
									<option value="">选择标的</option>
									{p.data.positions.map((row) => (
										<option key={row.symbol} value={row.symbol}>
											{row.name} · {row.quantity} 股 / 份
										</option>
									))}
								</select>
							</div>
							<div className="field">
								<label htmlFor="adjust-kind">调整类型</label>
								<select
									id="adjust-kind"
									value={kind}
									onChange={(e) => setKind(e.target.value)}
								>
									<option value="dividend">现金分红</option>
									<option value="split">拆股 / 合股</option>
								</select>
							</div>
							<div className="field">
								<label htmlFor="adjust-date">生效日期</label>
								<input
									id="adjust-date"
									type="date"
									required
									value={date}
									onChange={(e) => setDate(e.target.value)}
								/>
							</div>
							{kind === "dividend" ? (
								<>
									<div className="field">
										<label htmlFor="adjust-cash">
											税前每股股息（{position?.currency ?? "持仓币种"}）
										</label>
										<input
											id="adjust-cash"
											type="number"
											min="0.000001"
											step="0.000001"
											required
											value={cash}
											onChange={(e) => setCash(e.target.value)}
										/>
									</div>
									<div className="field">
										<label htmlFor="adjust-payment">收款状态</label>
										<select
											id="adjust-payment"
											value={payment}
											onChange={(e) => setPayment(e.target.value)}
										>
											<option value="paid">已收到，增加现金</option>
											<option value="receivable">应收，待到账后确认</option>
										</select>
									</div>
								</>
							) : (
								<>
									<div className="field">
										<label htmlFor="adjust-numerator">调整后份数</label>
										<input
											id="adjust-numerator"
											type="number"
											min="1"
											max="10000"
											step="1"
											required
											value={numerator}
											onChange={(e) => setNumerator(e.target.value)}
										/>
									</div>
									<div className="field">
										<label htmlFor="adjust-denominator">对应调整前份数</label>
										<input
											id="adjust-denominator"
											type="number"
											min="1"
											max="10000"
											step="1"
											required
											value={denominator}
											onChange={(e) => setDenominator(e.target.value)}
										/>
									</div>
								</>
							)}
						</div>
						<div className="field">
							<label htmlFor="adjust-note">公告来源与核对依据</label>
							<textarea
								id="adjust-note"
								minLength={5}
								maxLength={2000}
								required
								value={note}
								onChange={(e) => setNote(e.target.value)}
							/>
						</div>
					</fieldset>
					<div className="preview-box">
						<h3>核对登记内容</h3>
						<p>
							{symbol || "尚未选择持仓"} · 当前 {position?.quantity ?? "—"} 股 /
							份 · 生效 {date || "尚未填写"}
						</p>
						<p>
							{kind === "dividend"
								? `税前每股股息 ${cash || "—"} ${position?.currency ?? ""}，${payment === "paid" ? "已收款" : "仅登记应收"}`
								: `每 ${denominator} 份变为 ${numerator} 份，总成本保留`}
						</p>
					</div>
					<label className="confirm-check">
						<input
							type="checkbox"
							checked={confirmed !== null}
							disabled={!online || save.isPending}
							onChange={(e) => {
								e.stopPropagation();
								setConfirmed(
									e.target.checked && position && p.data
										? { quantity: position.quantity, round: p.data.round_id }
										: null,
								);
							}}
						/>
						我已核对持仓、日期和公告依据，确认登记且不能删除
					</label>
					{save.isError && <ErrorNotice message={errorMessage(save.error)} />}{" "}
					{save.isSuccess && (
						<p role="status" className="positive">
							调整已登记，下方可查看完整记录。
						</p>
					)}
					<button
						className="button primary"
						type="submit"
						disabled={!confirmed || !online || save.isPending || !position}
					>
						确认登记公司行动
					</button>
				</form>
			)}
			<AdjustmentRecords />
		</Modal>
	);
}
