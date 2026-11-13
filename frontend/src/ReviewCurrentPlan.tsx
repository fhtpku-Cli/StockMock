import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, errorMessage } from "./api";
import { PlanSummary } from "./PlanSummary";
import { planSchema } from "./planSchema";
import type { ReviewDetailData } from "./reviewSchema";
import { ErrorNotice } from "./ui";
import { useOnline } from "./useOnline";

export function ReviewCurrentPlan({
	detail,
}: {
	readonly detail: ReviewDetailData;
}) {
	const plan = detail.review.plan;
	const online = useOnline();
	const client = useQueryClient();
	const [ending, setEnding] = useState(false);
	const end = useMutation({
		mutationFn: () =>
			api(
				`plans/${encodeURIComponent(plan?.id ?? "")}`,
				planSchema,
				plan
					? {
							symbol: plan.symbol,
							direction: plan.direction,
							status: "ended",
							reason: plan.reason,
							target_price: plan.target_price,
							stop_price: plan.stop_price,
							review_date: plan.review_date,
							invalidation: plan.invalidation,
							version: plan.version,
						}
					: {},
				{ method: "put" },
			),
		onSuccess: async () => {
			setEnding(false);
			await Promise.all(
				["reviews", "review-detail", "review-facts", "plans", "alerts"].map(
					(key) => client.invalidateQueries({ queryKey: [key] }),
				),
			);
		},
	});
	return (
		<>
			{plan && (
				<section>
					<h3>当前计划</h3>
					<PlanSummary plan={plan} />
					{!detail.read_only && plan.status !== "ended" && (
						<>
							{ending ? (
								<div className="notice">
									<p>
										结束后计划内容只读，未完成的委托不会自动撤销，持仓不会自动卖出；仍可补充正式结论。
									</p>
									<div className="button-row">
										<button
											type="button"
											className="button"
											disabled={end.isPending}
											onClick={() => setEnding(false)}
										>
											继续保留
										</button>
										<button
											type="button"
											className="button primary"
											disabled={!online || end.isPending}
											onClick={() => end.mutate()}
										>
											确认结束计划
										</button>
									</div>
								</div>
							) : (
								<button
									type="button"
									className="button small"
									disabled={!online}
									onClick={() => setEnding(true)}
								>
									结束本计划
								</button>
							)}
							{end.isError && <ErrorNotice message={errorMessage(end.error)} />}
						</>
					)}
				</section>
			)}
		</>
	);
}
