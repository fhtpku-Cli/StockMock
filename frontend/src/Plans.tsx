import { useState } from "react";
import { errorMessage, price, time } from "./api";
import { Pagination } from "./Pagination";
import { PlanForm } from "./PlanForm";
import { PlanSummary } from "./PlanSummary";
import type { Plan } from "./planSchema";
import { planSchema } from "./planSchema";
import { ErrorNotice, Loading } from "./ui";
import { useOnline } from "./useOnline";
import { usePages } from "./usePages";

export function Plans({
	symbol,
	currency,
}: {
	readonly symbol: string;
	readonly currency: string;
}) {
	const online = useOnline();
	const plans = usePages(
		["plans", symbol],
		`plans?symbol=${encodeURIComponent(symbol)}`,
		planSchema,
	);
	const [editing, setEditing] = useState<Plan | "new" | null>(null);
	const [saved, setSaved] = useState(false);
	return (
		<section className="plans-section">
			<div className="section-heading">
				<h3>我的交易计划</h3>
				{editing === null && (
					<button
						type="button"
						className="button small"
						disabled={!online}
						onClick={() => {
							setSaved(false);
							setEditing("new");
						}}
					>
						新建计划
					</button>
				)}
			</div>
			<p className="footnote">
				计划可先于下单保存，并在你的设备间同步。参考价用于提醒与复盘；仅进行中的计划观察行情。
			</p>
			{saved && (
				<p role="status" className="positive">
					计划已保存。
				</p>
			)}
			{editing !== null && (
				<PlanForm
					key={editing === "new" ? "new" : `${editing.id}-${editing.version}`}
					symbol={symbol}
					currency={currency}
					plan={editing === "new" ? null : editing}
					onSaved={() => {
						setEditing(null);
						setSaved(true);
					}}
					onReload={(latest) =>
						setEditing(latest.status === "ended" ? null : latest)
					}
					onCancel={() => setEditing(null)}
				/>
			)}
			{plans.isPending ? (
				<Loading />
			) : (
				plans.isError && (
					<ErrorNotice
						message={errorMessage(plans.error)}
						retry={() => void plans.refetch()}
					/>
				)
			)}
			{plans.isSuccess && plans.items.length === 0 && (
				<p className="muted">尚未记录计划。可先保存为草稿，想好后再开始。</p>
			)}
			<div className="plan-list">
				{plans.items.map((plan) => (
					<article className="plan-record" key={plan.id}>
						<PlanSummary plan={plan} />
						{plan.status === "active" && (
							<div className="notice">
								<strong>
									{plan.monitoring.status === "valid"
										? "最新观察"
										: "暂不能判断是否到价"}
								</strong>
								<p>{plan.monitoring.message || "正在等待可用的有效行情。"}</p>
								{plan.monitoring.price && (
									<p>
										样本报价 {price(plan.monitoring.price, plan.currency)} ·
										市场时间 {time(plan.monitoring.market_time)}（北京时间） ·{" "}
										{plan.monitoring.source || "未记录来源"}
									</p>
								)}
								<p className="footnote">
									只根据实际观察到的有效样本提示，不代表精准触价或连续监控。
								</p>
							</div>
						)}
						{plan.status === "ended" ? (
							<p className="footnote">
								计划已结束，内容只读。新的判断请新建计划。
							</p>
						) : (
							<button
								type="button"
								className="button small"
								disabled={!online || editing !== null}
								onClick={() => {
									setSaved(false);
									setEditing(plan);
								}}
							>
								编辑计划
							</button>
						)}
					</article>
				))}
			</div>
			{plans.data && (
				<Pagination
					count={plans.items.length}
					total={plans.total}
					pending={plans.isFetchingNextPage}
					more={plans.hasNextPage}
					onMore={() => void plans.fetchNextPage()}
				/>
			)}
		</section>
	);
}
