import { useEffect, useId, useMemo, useState } from "react";
import { fullTime, price } from "./api";
import { OrderRecord } from "./OrderRecord";
import { Pagination } from "./Pagination";
import { PlanComparison } from "./PlanComparison";
import type { PlanSnapshot } from "./planSchema";
import type { Order } from "./schema";
import { Badge } from "./ui";

const statuses = {
	accepted: { label: "等待成交", filled: false },
	cancel_pending: { label: "撤单确认中", filled: false },
	filled: { label: "已成交", filled: true },
	cancelled: { label: "已撤单", filled: false },
	expired: { label: "已过期", filled: false },
	terminated: { label: "已终止", filled: false },
} as const satisfies Record<
	Order["status"],
	{ readonly label: string; readonly filled: boolean }
>;
const sides = { buy: "买入", sell: "卖出" } as const;

export function sortReviewOrders(orders: readonly Order[]): Order[] {
	return [...orders].sort((left, right) => {
		const timeDifference =
			Date.parse(left.created_at) - Date.parse(right.created_at);
		if (timeDifference) return timeDifference;
		const leftFraction = /\.(\d+)/.exec(left.created_at)?.[1] ?? "";
		const rightFraction = /\.(\d+)/.exec(right.created_at)?.[1] ?? "";
		const digits = Math.max(leftFraction.length, rightFraction.length);
		const fractionDifference = leftFraction
			.padEnd(digits, "0")
			.localeCompare(rightFraction.padEnd(digits, "0"));
		return (
			fractionDifference ||
			(left.id < right.id ? -1 : left.id > right.id ? 1 : 0)
		);
	});
}

export function ReviewOrderSummary({ order }: { readonly order: Order }) {
	const status = statuses[order.status];
	return (
		<div className="review-order-summary">
			<div className="review-order-heading">
				<strong>
					{sides[order.side]} {order.quantity} 股 / 份
				</strong>
				<Badge>{status.label}</Badge>
				<small>编号 {order.id.slice(0, 8)}</small>
			</div>
			<dl>
				<div>
					<dt>提交时间</dt>
					<dd>{fullTime(order.created_at)}（北京时间）</dd>
				</div>
				<div>
					<dt>下单计划</dt>
					<dd>
						{order.plan_snapshot
							? `第 ${order.plan_snapshot.version} 版`
							: "当时计划未保存"}
					</dd>
				</div>
				{status.filled ? (
					<>
						<div>
							<dt>成交价格 · {order.currency}</dt>
							<dd>
								{order.fill_price === null
									? "未记录"
									: price(order.fill_price, order.currency)}
							</dd>
						</div>
						<div>
							<dt>市场成交时间</dt>
							<dd>
								{order.filled_at
									? `${fullTime(order.filled_at)}（北京时间）`
									: "未记录"}
							</dd>
						</div>
					</>
				) : (
					<div>
						<dt>成交情况</dt>
						<dd>尚未成交</dd>
					</div>
				)}
			</dl>
		</div>
	);
}

export function ReviewOrders({
	orders,
	current,
}: {
	readonly orders: readonly Order[];
	readonly current: PlanSnapshot | null;
}) {
	const sorted = useMemo(() => sortReviewOrders(orders), [orders]);
	const [count, setCount] = useState(20);
	const [orderId, setOrderId] = useState(sorted[0]?.id ?? "");
	const selected = sorted.find((order) => order.id === orderId) ?? sorted[0];
	const detailsId = useId();
	const visible = sorted.slice(0, count);
	useEffect(() => {
		setOrderId(selected?.id ?? "");
	}, [selected?.id]);
	return (
		<section aria-label="关联委托概览" className="review-orders">
			<h3>关联委托概览（{sorted.length} 笔）</h3>
			{sorted.length ? (
				<>
					<p className="footnote">
						按提交时间从早到晚排列；同一时间按完整委托编号排序。状态以本次读取的已保存记录为准。
					</p>
					<ul className="review-order-list">
						{visible.map((order) => (
							<li key={order.id} data-review-order-id={order.id}>
								<ReviewOrderSummary order={order} />
								<button
									type="button"
									className="button small"
									aria-pressed={selected?.id === order.id}
									aria-controls={detailsId}
									aria-label={`查看这笔依据 · ${sides[order.side]} ${order.quantity} · ${fullTime(order.created_at)} 北京时间 · ${order.id.slice(0, 8)}`}
									onClick={() => setOrderId(order.id)}
								>
									查看这笔依据{selected?.id === order.id ? " · 已选中" : ""}
								</button>
							</li>
						))}
					</ul>
					<Pagination
						count={visible.length}
						total={sorted.length}
						pending={false}
						more={visible.length < sorted.length}
						onMore={() => setCount((value) => value + 20)}
					/>
					{selected && (
						<section
							id={detailsId}
							aria-label="所选委托依据"
							className="review-order-evidence"
						>
							<h4>
								所选委托依据 · {sides[selected.side]} {selected.quantity} ·{" "}
								{selected.id.slice(0, 8)}
							</h4>
							<OrderRecord order={selected} expanded />
							<PlanComparison order={selected} current={current} />
						</section>
					)}
				</>
			) : (
				<p className="footnote">尚无关联委托，也可以主动记录观察结论。</p>
			)}
		</section>
	);
}
