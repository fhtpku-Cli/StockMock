import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { z } from "zod";
import { Adjustments } from "./Adjustments";
import { Archive } from "./Archive";
import { api, errorMessage, money, time } from "./api";
import {
	acknowledgmentSchema,
	fxSchema,
	portfolioSchema,
	roundsSchema,
} from "./schema";
import { ErrorNotice, Loading, Modal } from "./ui";
import { useOnline } from "./useOnline";
export function Settings({
	onClose,
	onLogout,
}: {
	readonly onClose: () => void;
	readonly onLogout: () => void;
}) {
	const online = useOnline();
	const [adjusting, setAdjusting] = useState(false);
	const [archiveId, setArchiveId] = useState<string | null>(null);
	const [confirmation, setConfirmation] = useState("");
	const [resetId] = useState(() => crypto.randomUUID());
	const client = useQueryClient();
	const rounds = useQuery({
		queryKey: ["rounds"],
		queryFn: () => api("rounds", roundsSchema),
	});
	const reset = useMutation({
		mutationFn: () =>
			api("rounds/reset", z.object({ round_id: z.string() }), {
				request_id: resetId,
				confirmation,
			}),
		onSuccess: async () => {
			await client.invalidateQueries();
			onClose();
		},
	});
	const logout = useMutation({
		mutationFn: () => api("auth/logout", acknowledgmentSchema, {}),
		onSuccess: () => {
			client.clear();
			onLogout();
		},
	});
	return (
		<>
			<Modal title="练习设置" onClose={onClose}>
				<div className="settings-section">
					<h3>个人空间</h3>
					<p>这里的资金与交易均为模拟，不连接真实证券账户。</p>
					<button
						type="button"
						className="button"
						onClick={() => logout.mutate()}
						disabled={logout.isPending}
					>
						退出登录
					</button>
					{logout.isError && (
						<ErrorNotice message={errorMessage(logout.error)} />
					)}
				</div>
				<div className="settings-section">
					<h3>公司行动</h3>
					<p>核对公告后，人工登记分红、拆股或合股，保留完整账本依据。</p>
					<button
						type="button"
						className="button"
						onClick={() => setAdjusting(true)}
					>
						分红与拆股
					</button>
					<h3>练习轮次</h3>
					{rounds.isPending ? (
						<Loading />
					) : rounds.isError ? (
						<ErrorNotice message={errorMessage(rounds.error)} />
					) : (
						[...rounds.data]
							.sort((a, b) => a.created_at.localeCompare(b.created_at))
							.map((r, index) => (
								<div key={r.id} className="round-row">
									<span>练习轮次 {index + 1}</span>
									<small>
										{time(r.created_at)} ·{" "}
										{r.archived_at ? "已归档" : "当前轮次"}
									</small>
									{r.archived_at && (
										<button
											type="button"
											className="text-button"
											onClick={() => setArchiveId(r.id)}
										>
											查看历史
										</button>
									)}
								</div>
							))
					)}
					<p className="footnote">
						结束当前轮次后保留历史记录，新轮次恢复 100
						万元本金。有进行中的委托时，请先等待结束或撤单。
					</p>
					<label htmlFor="reset-confirm">输入“重新开始”以确认</label>
					<input
						id="reset-confirm"
						value={confirmation}
						onChange={(e) => setConfirmation(e.target.value)}
						autoComplete="off"
					/>
					<button
						type="button"
						className="button danger"
						disabled={!online || confirmation !== "重新开始" || reset.isPending}
						onClick={() => reset.mutate()}
					>
						{reset.isPending ? "正在归档…" : "归档本轮，开始新练习"}
					</button>
					{reset.isError && <ErrorNotice message={errorMessage(reset.error)} />}
				</div>
				<p className="footnote">
					手机需先打开已配置的私人访问地址并登录；不能使用手机自己的
					localhost。iPhone 在 Safari 分享菜单选择“添加到主屏幕”，Android
					在浏览器菜单选择安装。交易需要联网，账户不做离线缓存。后台处理依赖电脑服务保持运行，电脑休眠或服务停止时不会继续处理。
				</p>
			</Modal>
			{adjusting && <Adjustments onClose={() => setAdjusting(false)} />}
			{archiveId && (
				<Archive roundId={archiveId} onClose={() => setArchiveId(null)} />
			)}
		</>
	);
}
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
		<Modal title="现金与换汇" onClose={onClose}>
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
							1 {rate.currency} = {rate.cny_per_unit} CNY
							<small>
								{rate.source} · {time(rate.market_time)}
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
