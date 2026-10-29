import { ArrowRight } from "@phosphor-icons/react";
import { number } from "./api";
import type { Portfolio } from "./schema";
import { Panel } from "./ui";

export function ActiveOrdersSummary({
	value,
	onOrders,
}: {
	readonly value: Portfolio["active_orders"];
	readonly onOrders: () => void;
}) {
	const total = value
		? value.accepted_count + value.cancel_pending_count
		: null;
	return (
		<Panel
			className="active-orders-summary"
			title={
				total === null
					? "委托摘要暂不可用"
					: total === 0
						? "暂无进行中委托"
						: `进行中委托${number(total)}笔`
			}
			action={
				<button type="button" className="text-button" onClick={onOrders}>
					查看进行中委托 <ArrowRight />
				</button>
			}
		>
			{value && (
				<p className="footnote">
					已受理{number(value.accepted_count)} · 撤单待确认
					{number(value.cancel_pending_count)}
				</p>
			)}
		</Panel>
	);
}
