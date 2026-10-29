import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, errorMessage, money } from "./api";
import { CashStatement } from "./CashStatement";
import { fxDate, fxRate } from "./fxDisplay";
import { fxSchema, portfolioSchema } from "./schema";
import { ErrorNotice, Loading, Modal } from "./ui";
import { useOnline } from "./useOnline";
export function Cash({ onClose }: { readonly onClose: () => void }) {
	const online = useOnline();
	const [currency, setCurrency] = useState<"USD" | "HKD">("USD");
	const [amount, setAmount] = useState("");
	const [requestId, setRequestId] = useState(() => crypto.randomUUID());
	const client = useQueryClient();
	const portfolio = useQuery({
		queryKey: ["portfolio"],
		queryFn: () => api("portfolio", portfolioSchema),
	});
	const fx = useQuery({ queryKey: ["fx"], queryFn: () => api("fx", fxSchema) });
	const convert = useMutation({
		mutationFn: () =>
			api("fx/convert", portfolioSchema, {
				request_id: requestId,
				currency,
				amount,
			}),
		onSuccess: async () => {
			setAmount("");
			setRequestId(crypto.randomUUID());
			await client.invalidateQueries({ queryKey: ["portfolio"] });
			await client.invalidateQueries({ queryKey: ["ledger"] });
		},
	});
	return (
		<Modal title="现金与换汇" onClose={onClose} wide>
			<CashStatement />
			{portfolio.isSuccess && (
				<dl className="summary-list">
					{portfolio.data.cash.map((c) => (
						<div key={c.currency}>
							<dt>{c.currency} 可用 / 占用</dt>
							<dd>
								{money(c.available, c.currency)} /{" "}
								{money(c.reserved, c.currency)}
							</dd>
						</div>
					))}
				</dl>
			)}
			{portfolio.isError && (
				<ErrorNotice message={errorMessage(portfolio.error)} />
			)}
			<h3>外币换回人民币</h3>
			<p className="footnote">
				买入时会自动补足外币。卖出后保留的美元、港币可在这里换回。
			</p>
			{fx.isPending ? (
				<Loading />
			) : fx.isError ? (
				<ErrorNotice
					message={errorMessage(fx.error)}
					retry={() => void fx.refetch()}
				/>
			) : (
				<div className="fx-rates">
					{fx.data.map((rate) => (
						<p key={rate.currency}>
							1 {rate.currency} = {fxRate(rate.cny_per_unit)} CNY
							<small>
								{rate.source} · {fxDate(rate.market_time)}
							</small>
						</p>
					))}
				</div>
			)}
			<form
				onSubmit={(e) => {
					e.preventDefault();
					convert.mutate();
				}}
			>
				<fieldset disabled={!online || convert.isPending}>
					<div className="form-grid">
						<div className="field">
							<label htmlFor="fx-currency">卖出币种</label>
							<select
								id="fx-currency"
								value={currency}
								onChange={(e) => {
									setCurrency(e.target.value === "HKD" ? "HKD" : "USD");
									setRequestId(crypto.randomUUID());
									convert.reset();
								}}
							>
								<option value="USD">美元 USD</option>
								<option value="HKD">港币 HKD</option>
							</select>
						</div>
						<div className="field">
							<label htmlFor="fx-amount">换回金额</label>
							<input
								id="fx-amount"
								type="number"
								inputMode="decimal"
								min="0.01"
								step="0.01"
								required
								value={amount}
								onChange={(e) => {
									setAmount(e.target.value);
									setRequestId(crypto.randomUUID());
									convert.reset();
								}}
							/>
						</div>
					</div>
				</fieldset>
				{convert.isError && (
					<ErrorNotice message={errorMessage(convert.error)} />
				)}{" "}
				{convert.isSuccess && (
					<p role="status" className="positive">
						换汇已完成，资金流水已记录。
					</p>
				)}
				<button
					type="submit"
					className="button primary full"
					disabled={!online || convert.isPending || !fx.isSuccess}
				>
					{convert.isPending ? "换汇中…" : "确认换回人民币"}
				</button>
			</form>
		</Modal>
	);
}
