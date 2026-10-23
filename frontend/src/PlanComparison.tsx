import { type PlanSnapshot, planDirections } from "./planSchema";
import type { Order } from "./schema";

const fields = [
	["direction", "方向"],
	["reason", "理由"],
	["target_price", "目标参考价"],
	["stop_price", "止损参考价"],
	["review_date", "复盘日期（北京时间）"],
	["invalidation", "失效条件"],
] as const;
export function PlanComparison({
	order,
	current,
}: {
	readonly order: Order;
	readonly current: PlanSnapshot | null;
}) {
	const snapshot = order.plan_snapshot;
	if (!snapshot)
		return (
			<p className="notice">
				未保存当时计划。原订单理由：{order.reason || "未记录"}
				。不能用当前计划补造历史。
			</p>
		);
	return (
		<section className="plan-comparison" aria-label="下单依据对照">
			<p className="footnote">
				参考价币种：{order.currency}。对照保留当时记录，不补造版本历史。
			</p>
			<h4>
				下单时第 {snapshot.version} 版 · 当前
				{current ? `第 ${current.version} 版` : "计划未知"}
			</h4>
			{fields.map(([field, label]) => {
				const previous =
					field === "direction"
						? planDirections[snapshot.direction]
						: snapshot[field];
				const latest = current
					? field === "direction"
						? planDirections[current.direction]
						: current[field]
					: null;
				return (
					<div className="comparison-row" key={field}>
						<strong>
							{label}
							{current && snapshot[field] !== current[field] ? " · 已变化" : ""}
						</strong>
						<div>
							<small>下单当时</small>
							<p>{previous || "未记录"}</p>
						</div>
						<div>
							<small>当前计划</small>
							<p>{latest || "未记录"}</p>
						</div>
					</div>
				);
			})}
		</section>
	);
}
