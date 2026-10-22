import { useMutation } from "@tanstack/react-query";
import { api, errorMessage } from "./api";
import { Pagination } from "./Pagination";
import type { Plan } from "./planSchema";
import { planDirections, planSchema, planStates } from "./planSchema";
import { ErrorNotice, Loading } from "./ui";
import { usePages } from "./usePages";

export function PlanPicker({
	symbol,
	selected,
	onSelect,
}: {
	readonly symbol: string;
	readonly selected: Plan | null;
	readonly onSelect: (plan: Plan | null) => void;
}) {
	const plans = usePages(
		["plans", "order", symbol],
		`plans?symbol=${encodeURIComponent(symbol)}`,
		planSchema,
	);
	const reload = useMutation({
		mutationFn: () =>
			api(`plans/${encodeURIComponent(selected?.id ?? "")}`, planSchema),
		onSuccess: (plan) => onSelect(plan.status === "ended" ? null : plan),
	});
	const available = plans.items.filter((plan) => plan.status !== "ended");
	return (
		<div className="field">
			<label htmlFor="order-plan">
				带入已有计划 <span>选填</span>
			</label>
			{plans.isPending ? (
				<Loading />
			) : plans.isError ? (
				<ErrorNotice
					message={errorMessage(plans.error)}
					retry={() => void plans.refetch()}
				/>
			) : (
				<>
					<select
						id="order-plan"
						value={selected?.id ?? ""}
						onChange={(event) =>
							onSelect(
								available.find((plan) => plan.id === event.target.value) ??
									null,
							)
						}
					>
						<option value="">不关联计划</option>
						{available.map((plan) => (
							<option key={plan.id} value={plan.id}>
								{planStates[plan.status]} · {planDirections[plan.direction]} ·{" "}
								{plan.reason.slice(0, 36) || "未填写理由"}
							</option>
						))}
					</select>
					{plans.hasNextPage && (
						<Pagination
							count={plans.items.length}
							total={plans.total}
							pending={plans.isFetchingNextPage}
							more={plans.hasNextPage}
							onMore={() => void plans.fetchNextPage()}
						/>
					)}
				</>
			)}
			<p className="footnote">
				选择后带入理由与参考价，仍需填写数量并预览后确认下单。修改下方交易记录不会改写计划；下单时会保存当时的计划。
			</p>
			{selected && (
				<div className="notice">
					<p>
						已带入 {planDirections[selected.direction]}{" "}
						计划。若计划在另一台设备修改，请重新载入最新计划并预览。
					</p>
					<button
						type="button"
						className="button small"
						disabled={reload.isPending}
						onClick={() => reload.mutate()}
					>
						{reload.isPending ? "载入中…" : "重新载入所选计划"}
					</button>
					{reload.isError && (
						<ErrorNotice message={errorMessage(reload.error)} />
					)}
				</div>
			)}
		</div>
	);
}
