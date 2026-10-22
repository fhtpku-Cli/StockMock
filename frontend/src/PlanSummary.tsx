import { price, time } from "./api";
import type { PlanSnapshot } from "./planSchema";
import { planDirections, planStates } from "./planSchema";
import { Badge } from "./ui";
export function PlanSummary({
	plan,
	snapshot = false,
}: {
	readonly plan: PlanSnapshot;
	readonly snapshot?: boolean;
}) {
	return (
		<div className="plan-summary">
			<div className="plan-heading">
				<strong>
					{plan.symbol} · {planDirections[plan.direction]}
				</strong>
				<Badge tone={plan.status === "active" ? "good" : "neutral"}>
					{planStates[plan.status]}
				</Badge>
			</div>
			{snapshot && (
				<p className="footnote">
					以下为下单当时保存的计划，后续修改不会改变这份记录。
				</p>
			)}
			<dl className="summary-list">
				<div>
					<dt>理由</dt>
					<dd>{plan.reason || "未记录"}</dd>
				</div>
				<div>
					<dt>目标参考价</dt>
					<dd>
						{plan.target_price
							? price(plan.target_price, plan.currency)
							: "未记录"}
					</dd>
				</div>
				<div>
					<dt>止损参考价</dt>
					<dd>
						{plan.stop_price ? price(plan.stop_price, plan.currency) : "未记录"}
					</dd>
				</div>
				<div>
					<dt>计划复盘日期</dt>
					<dd>
						{plan.review_date ? `${plan.review_date}（北京时间）` : "未记录"}
					</dd>
				</div>
				<div>
					<dt>失效条件</dt>
					<dd>{plan.invalidation || "未记录"}</dd>
				</div>
				<div>
					<dt>{snapshot ? "当时修订时间" : "最后修改"}</dt>
					<dd>{time(plan.updated_at)}（北京时间）</dd>
				</div>
			</dl>
		</div>
	);
}
