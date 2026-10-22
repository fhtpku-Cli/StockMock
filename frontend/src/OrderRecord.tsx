import type { ReactNode } from "react";
import type { Order } from "./schema";
import { money, price, time } from "./api";
import { Badge } from "./ui";
import { OrderDetails } from "./OrderDetails";
const statuses = {
	accepted: "等待成交",
	cancel_pending: "撤单确认中",
	filled: "已成交",
	cancelled: "已撤单",
	expired: "已过期",
	terminated: "已终止",
} as const;
export function OrderRecord({
	order: o,
	actions,
	expanded = false,
}: {
	readonly order: Order;
	readonly actions?: ReactNode;
	readonly expanded?: boolean;
}) {
	return (
		<article className="order-row">
			<div className="order-top">
				<div>
					<strong>{o.symbol}</strong>
					<span className={o.side === "buy" ? "positive" : "negative"}>
						{o.side === "buy" ? "买入" : "卖出"} {o.quantity} 股 / 份
					</span>
				</div>
				<Badge
					tone={
						o.status === "filled"
							? "good"
							: o.status === "accepted" || o.status === "cancel_pending"
								? "warn"
								: "neutral"
					}
				>
					{statuses[o.status]}
				</Badge>
			</div>
			<div className="order-details">
				<span>
					{o.order_type === "limit" ? "限价" : "市价模拟"}{" "}
					{price(o.limit_price ?? o.price_bound, o.currency)}
				</span>
				<span>提交 {time(o.created_at)}</span>
				{o.fill_price && <span>成交 {price(o.fill_price, o.currency)}</span>}
				<span>
					费用{" "}
					{o.fee !== null
						? money(o.fee, o.currency)
						: o.status === "filled"
							? "历史值未记录"
							: "尚未成交"}
				</span>
			</div>
			{o.status_reason && <p className="footnote">{o.status_reason}</p>}
			<details open={expanded || undefined}>
				<summary>时间与交易计划</summary>
				<OrderDetails order={o} />
			</details>
			{actions && <div className="order-actions">{actions}</div>}
		</article>
	);
}
export function LinkedTrade({
	order,
	linked,
}: {
	readonly order: Order | undefined;
	readonly linked: boolean;
}) {
	if (!linked) return null;
	if (!order)
		return (
			<p className="footnote">
				关联委托暂未加载或不在当前展示范围内，原计划与成交详情未知。
			</p>
		);
	return (
		<details>
			<summary>原计划与实际交易 · {order.symbol}</summary>
			<OrderRecord order={order} expanded />
		</details>
	);
}
