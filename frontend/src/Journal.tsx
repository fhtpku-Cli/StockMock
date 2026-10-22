import { z } from "zod";
import { orderSchema } from "./schema";
import {
	RecordFilters,
	initialRecordFilter,
	recordMatches,
} from "./RecordFilters";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Notebook, DownloadSimple } from "@phosphor-icons/react";
import { api, errorMessage, money, time } from "./api";
import { acknowledgmentSchema, journalSchema, ledgerSchema } from "./schema";
import { Empty, ErrorNotice, Loading, Panel } from "./ui";
export function Journal({
	orderId,
	onSaved,
}: {
	readonly orderId: string | null;
	readonly onSaved: () => void;
}) {
	const [recordFilter, setRecordFilter] = useState(initialRecordFilter);
	const orders = useQuery({
		queryKey: ["orders"],
		queryFn: () => api("orders", z.array(orderSchema)),
	});
	const [text, setText] = useState("");
	const client = useQueryClient();
	const journal = useQuery({
		queryKey: ["journal"],
		queryFn: () => api("journal", journalSchema),
	});
	const ledger = useQuery({
		queryKey: ["ledger"],
		queryFn: () => api("ledger", ledgerSchema),
	});
	const save = useMutation({
		mutationFn: () =>
			api("journal", acknowledgmentSchema, {
				text,
				...(orderId ? { order_id: orderId } : {}),
			}),
		onSuccess: async () => {
			setText("");
			onSaved();
			await client.invalidateQueries({ queryKey: ["journal"] });
		},
	});
	return (
		<div className="stack">
			<div className="journal-intro">
				<Notebook size={36} weight="duotone" />
				<div>
					<h2>把每一次判断，留给未来的自己。</h2>
					<p>记录当时的理由，也记录后来改变的看法。</p>
				</div>
			</div>
			<RecordFilters value={recordFilter} onChange={setRecordFilter} />
			<p className="footnote">
				市场按关联委托币种筛选；未关联笔记仅在“全部”显示。资金流水按币种筛选。
			</p>
			{orders.isError && recordFilter.market !== "all" && (
				<ErrorNotice
					message="关联委托读取失败，无法准确筛选笔记，请重试。"
					retry={() => void orders.refetch()}
				/>
			)}
			<div className="dashboard-grid">
				<div className="stack">
					<Panel title="写下这一刻的判断">
						<form
							onSubmit={(e) => {
								e.preventDefault();
								save.mutate();
							}}
						>
							{orderId && <p className="notice">关联委托 {orderId}</p>}
							<label className="sr-only" htmlFor="journal-text">
								复盘内容
							</label>
							<textarea
								id="journal-text"
								className="journal-input"
								placeholder="今天做了什么决策？依据是什么？有什么值得下次注意？"
								required
								minLength={1}
								maxLength={5000}
								value={text}
								onChange={(e) => setText(e.target.value)}
								disabled={save.isPending}
							/>
							<div className="form-footer">
								<span className="footnote">留给自己，不公开分享</span>
								<button
									type="submit"
									className="button primary"
									disabled={save.isPending || !text.trim()}
								>
									{save.isPending ? "保存中…" : "保存记录"}
								</button>
							</div>
							{save.isError && (
								<ErrorNotice message={errorMessage(save.error)} />
							)}{" "}
							{save.isSuccess && (
								<p role="status" className="positive">
									已保存到本轮复盘。
								</p>
							)}
						</form>
					</Panel>
					<Panel title="复盘笔记">
						{journal.isPending ? (
							<Loading />
						) : journal.isError ? (
							<ErrorNotice
								message={errorMessage(journal.error)}
								retry={() => void journal.refetch()}
							/>
						) : journal.data.length === 0 ? (
							<Empty title="给决策留一点文字">
								收益告诉你结果，记录帮你理解过程。
							</Empty>
						) : (
							<div className="journal-list">
								{journal.data
									.filter((j) =>
										recordMatches(
											j.created_at,
											orders.data?.find((o) => o.id === j.order_id)?.currency,
											recordFilter,
										),
									)
									.map((j) => (
										<article key={j.id}>
											<small>
												{time(j.created_at)}（北京时间）
												{j.order_id ? " · 关联交易" : ""}
											</small>
											<p>{j.text}</p>
										</article>
									))}
							</div>
						)}
					</Panel>
				</div>
				<Panel
					title="资金流水"
					action={
						<a
							className="text-button"
							href="/api/export.csv"
							download
							aria-label="导出资金流水"
						>
							<DownloadSimple size={20} />
						</a>
					}
				>
					{ledger.isPending ? (
						<Loading />
					) : ledger.isError ? (
						<ErrorNotice
							message={errorMessage(ledger.error)}
							retry={() => void ledger.refetch()}
						/>
					) : ledger.data.length === 0 ? (
						<p className="muted">暂无资金流水。</p>
					) : (
						<div className="ledger-list">
							{ledger.data
								.filter((l) =>
									recordMatches(l.created_at, l.currency, recordFilter),
								)
								.map((l) => (
									<article key={l.id}>
										<div>
											<strong>{l.note || l.kind}</strong>
											<small>{time(l.created_at)}</small>
										</div>
										<b>{money(l.amount, l.currency)}</b>
									</article>
								))}
						</div>
					)}
				</Panel>
			</div>
		</div>
	);
}
