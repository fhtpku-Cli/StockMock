import { Clock } from "@phosphor-icons/react";
import {
	type QueryClient,
	queryOptions,
	useQuery,
	useQueryClient,
} from "@tanstack/react-query";
import { useEffect, useState } from "react";
import type { z } from "zod";
import { api, time } from "./api";
import { healthSchema } from "./schema";
import { ErrorNotice } from "./ui";

type ObservedHealth = z.infer<typeof healthSchema> & {
	readonly tradeObservedAt: number | null;
	readonly planObservedAt: number | null;
};

export function backgroundStatusQuery(client: QueryClient) {
	return queryOptions({
		queryKey: ["health"],
		queryFn: async ({ signal }): Promise<ObservedHealth> => {
			const health = await api("health", healthSchema, undefined, { signal });
			const previous = client.getQueryData<ObservedHealth>(["health"]);
			const now = Date.now();
			return {
				...health,
				tradeObservedAt: health.last_run
					? null
					: (previous?.tradeObservedAt ?? now),
				planObservedAt: health.plan_worker.last_run
					? null
					: (previous?.planObservedAt ?? now),
			};
		},
		gcTime: Infinity,
		refetchInterval: 30000,
	});
}

function WorkerStatus({
	name,
	status,
	lastRun,
	observedAt,
}: {
	readonly name: string;
	readonly status: string;
	readonly lastRun: string | null;
	readonly observedAt: number | null;
}) {
	const [now, setNow] = useState(Date.now);
	const deadline =
		(lastRun ? Date.parse(lastRun) : (observedAt ?? now)) + 180000;
	useEffect(() => {
		const timer = window.setTimeout(
			() => setNow(Date.now()),
			Math.max(0, deadline - Date.now()),
		);
		return () => window.clearTimeout(timer);
	}, [deadline]);
	const delayed = now >= deadline;
	return (
		<div className="worker">
			<Clock size={16} />
			<span>
				{name} ·{" "}
				{delayed && (status === "running" || status === "starting")
					? "可能延迟"
					: status === "running"
						? "正常"
						: status === "starting"
							? "准备中"
							: status.startsWith(name)
								? status.slice(name.length)
								: status}
				{" · "}
				{time(lastRun)}
				{delayed && " · 已超过三分钟未见更新，请刷新核对"}
			</span>
		</div>
	);
}

export function BackgroundStatus() {
	const client = useQueryClient();
	const query = useQuery(backgroundStatusQuery(client));
	return (
		<section aria-label="后台处理状态">
			{query.isError && (
				<ErrorNotice
					message="后台状态暂时无法刷新。已有状态为上次读取结果，请重试核对。"
					retry={() => void query.refetch()}
				/>
			)}
			{query.isPending && <p className="footnote">正在读取后台状态…</p>}
			{query.data && (
				<>
					<WorkerStatus
						name="交易处理"
						status={query.data.status}
						lastRun={query.data.last_run}
						observedAt={query.data.tradeObservedAt}
					/>
					<WorkerStatus
						name="计划观察"
						status={query.data.plan_worker.status}
						lastRun={query.data.plan_worker.last_run}
						observedAt={query.data.planObservedAt}
					/>
				</>
			)}
		</section>
	);
}
