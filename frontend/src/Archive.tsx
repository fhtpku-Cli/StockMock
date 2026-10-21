import { useQuery } from "@tanstack/react-query";
import { z } from "zod";
import { api, errorMessage, money, price, time } from "./api";
import { decimal, journalSchema, ledgerSchema, orderSchema } from "./schema";
import { Badge, ErrorNotice, Loading, Modal } from "./ui";
const archiveSchema = z.object({
	id: z.string(),
	created_at: z.string(),
	archived_at: z.string().nullable(),
	cash: z.record(z.string(), decimal),
	orders: z.array(orderSchema),
	journal: journalSchema,
	ledger: ledgerSchema,
	truncated: z.boolean(),
});
export function Archive({
	roundId,
	onClose,
}: {
	readonly roundId: string;
	readonly onClose: () => void;
}) {
	const query = useQuery({
		queryKey: ["round", roundId],
		queryFn: () => api(`rounds/${encodeURIComponent(roundId)}`, archiveSchema),
	});
	return (
		<Modal title="已归档练习 · 只读" onClose={onClose} wide>
			{query.isPending ? (
				<Loading />
			) : query.isError ? (
				<ErrorNotice
					message={errorMessage(query.error)}
					retry={() => void query.refetch()}
				/>
			) : (
				<div className="stack">
					<div>
						<Badge>历史轮次</Badge>
						<p className="footnote">
							开始 {time(query.data.created_at)} · 归档{" "}
							{time(query.data.archived_at)}（北京时间）
						</p>
						<p className="notice">
							这是此前练习的历史记录，不影响当前账户，也不能在此修改或交易。
						</p>
					</div>
					<a
						className="text-button"
						href={`/api/rounds/${encodeURIComponent(roundId)}/export.csv`}
						download
					>
						导出本轮完整资金流水
					</a>
					{query.data.truncated && (
						<p className="notice">
							记录较多，每类最多展示最近 500 条；完整资金流水可导出查看。
						</p>
					)}
					<section>
						<h3>归档时现金余额</h3>
						<dl className="summary-list">
							{Object.entries(query.data.cash).map(([currency, balance]) => (
								<div key={currency}>
									<dt>{currency}</dt>
									<dd>{money(balance, currency)}</dd>
								</div>
							))}
						</dl>
					</section>
					<section>
						<h3>历史委托（{query.data.orders.length}）</h3>
						{query.data.orders.length === 0 ? (
							<p className="footnote">本轮没有委托。</p>
						) : (
							query.data.orders.map((o) => (
								<article className="order-row" key={o.id}>
									<strong>
										{o.symbol} · {o.side === "buy" ? "买入" : "卖出"}{" "}
										{o.quantity}
									</strong>
									<p className="footnote">
										{time(o.created_at)} ·{" "}
										{o.fill_price
											? `成交 ${price(o.fill_price, o.currency)}`
											: `限价 ${price(o.limit_price ?? o.price_bound, o.currency)}`}{" "}
										· {o.status_reason}
									</p>
									<p>{o.reason || "未填写交易理由"}</p>
								</article>
							))
						)}
					</section>
					<section>
						<h3>历史复盘（{query.data.journal.length}）</h3>
						<div className="journal-list">
							{query.data.journal.length === 0 ? (
								<p className="footnote">本轮没有复盘笔记。</p>
							) : (
								query.data.journal.map((j) => (
									<article key={j.id}>
										<small>
											{time(j.created_at)}
											{j.order_id ? " · 关联交易" : ""}
										</small>
										<p>{j.text}</p>
									</article>
								))
							)}
						</div>
					</section>
					<section>
						<h3>历史资金流水（{query.data.ledger.length}）</h3>
						<div className="ledger-list">
							{query.data.ledger.length === 0 ? (
								<p className="footnote">本轮没有资金变动。</p>
							) : (
								query.data.ledger.map((l) => (
									<article key={l.id}>
										<div>
											<strong>{l.note}</strong>
											<small>{time(l.created_at)}</small>
										</div>
										<b>{money(l.amount, l.currency)}</b>
									</article>
								))
							)}
						</div>
					</section>
				</div>
			)}
		</Modal>
	);
}
