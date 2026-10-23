import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { z } from "zod";
import { ApiError, api, errorMessage, money, time } from "./api";
import { OrderRecord } from "./OrderRecord";
import { pageSchema } from "./planSchema";
import { type ledgerSchema, orderSchema } from "./schema";
import { ledgerKinds } from "./statementSchema";
import { ErrorNotice, Loading } from "./ui";

function LedgerOrder({
	id,
	roundId,
}: {
	readonly id: string;
	readonly roundId: string | undefined;
}) {
	const [open, setOpen] = useState(false);
	const query = useQuery({
		queryKey: ["ledger-order", roundId, id],
		queryFn: async () => {
			if (!roundId) {
				const orders = await api("orders", z.array(orderSchema));
				const found = orders.find((order) => order.id === id);
				if (found) return found;
			} else {
				let offset = 0;
				while (true) {
					const page = await api(
						`rounds/${encodeURIComponent(roundId)}/orders?offset=${offset}&limit=100`,
						pageSchema(orderSchema),
					);
					const found = page.items.find((order) => order.id === id);
					if (found) return found;
					offset += page.items.length;
					if (offset >= page.total || page.items.length === 0) break;
				}
			}
			throw new ApiError(
				"order_missing",
				"未找到此编号的关联委托，原始编号仍保留在导出中。",
			);
		},
		enabled: open,
	});
	return (
		<div>
			<button
				type="button"
				className="text-button"
				aria-expanded={open}
				onClick={() => setOpen(!open)}
			>
				{open ? "收起关联委托" : "查看关联委托"}
			</button>
			{open &&
				(query.isPending ? (
					<Loading />
				) : query.isError ? (
					<ErrorNotice
						message={errorMessage(query.error)}
						retry={() => void query.refetch()}
					/>
				) : (
					<OrderRecord order={query.data} expanded />
				))}
		</div>
	);
}
export function LedgerEntries({
	rows,
	roundId,
}: {
	readonly rows: z.infer<typeof ledgerSchema>;
	readonly roundId?: string;
}) {
	return (
		<div className="statement-entries">
			{rows.map((row) => (
				<article key={row.id} data-entry={row.id}>
					<div className="statement-entry-heading">
						<div>
							<strong>{row.note || ledgerKinds[row.kind] || row.kind}</strong>
							<small>
								{ledgerKinds[row.kind] ?? row.kind} · {time(row.created_at)}
								（北京时间）
							</small>
						</div>
						<b>
							{Number(row.amount) > 0 ? "+" : ""}
							{money(row.amount, row.currency)}
						</b>
					</div>
					{Number(row.amount) === 0 && (
						<p className="footnote">
							此记录未改变现金；应收登记在实际到账时才增加现金。
						</p>
					)}
					{row.order_id ? (
						<LedgerOrder id={row.order_id} roundId={roundId} />
					) : (
						<p className="footnote">无关联记录</p>
					)}
				</article>
			))}
		</div>
	);
}
