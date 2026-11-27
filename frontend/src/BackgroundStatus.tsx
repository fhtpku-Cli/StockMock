import { CaretDown, CaretUp, Clock } from "@phosphor-icons/react";
import {
	type QueryClient,
	queryOptions,
	useQuery,
	useQueryClient,
} from "@tanstack/react-query";
import { useEffect, useState } from "react";
import type { z } from "zod";
import { api, time } from "./api";
import { healthSchema, type Gap, type MarketId } from "./schema";
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

export const marketNames = {
	CN: "A股",
	HK: "港股",
	US: "美股",
	unknown: "时段无法确定",
} as const;

/** 中断涉及的市场；日历覆盖不到时用文字说明，不用颜色或图形。 */
export function gapMarketsText(markets: readonly MarketId[]): string {
	const known = markets.filter((market) => market !== "unknown");
	const unknown = markets.includes("unknown");
	if (known.length === 0) return unknown ? marketNames.unknown : "";
	const base = `涉及${known.map((market) => marketNames[market]).join("、")}`;
	return unknown ? `${base}，部分时段无法确定` : base;
}

/** 同一北京日期只写一次日期；跨日两端都带日期。 */
export function gapRangeText(gap: Gap): string {
	const start = time(gap.started_at);
	const end = time(gap.ended_at);
	return start.slice(0, 5) === end.slice(0, 5)
		? `${start}–${end.slice(6)}`
		: `${start}–${end}`;
}

/** 单条中断：MM/DD HH:mm–HH:mm（X 分钟，涉及市场）。 */
export function gapLineText(gap: Gap): string {
	const markets = gapMarketsText(gap.markets);
	return `${gapRangeText(gap)}（${gap.duration_minutes} 分钟${
		markets === "" ? "" : `，${markets}`
	}）`;
}

/** 汇总一句：次数为近 7 天全部检测到的间隔，最近一次取列表首条。 */
export function gapSummaryText(count: number, latest: Gap | null): string {
	const base = `近 7 天交易时段内后台中断 ${count} 次`;
	return latest === null
		? `${base}。`
		: `${base}，最近一次 ${gapLineText(latest)}。`;
}

export const GAP_RECOVERY_NOTE =
	"后台恢复后会按现有规则继续处理，结果与原因见委托记录。";

/** 展开后的明细：最多 5 条，超过时说明仅列最近 5 条。 */
export function GapDetails({
	count,
	gaps,
}: {
	readonly count: number;
	readonly gaps: readonly Gap[];
}) {
	return (
		<>
			<ul className="worker-gap-list" id="worker-gaps-detail">
				{gaps.slice(0, 5).map((gap) => (
					<li key={`${gap.started_at}-${gap.ended_at}`}>
						{gapLineText(gap)}
					</li>
				))}
			</ul>
			{count > gaps.length && (
				<p className="footnote">
					共 {count} 次，仅列出最近 {gaps.length} 次。
				</p>
			)}
		</>
	);
}

function WorkerGaps({
	count,
	gaps,
}: {
	readonly count: number;
	readonly gaps: readonly Gap[];
}) {
	const [open, setOpen] = useState(false);
	const latest = gaps[0] ?? null;
	return (
		<div className="worker-gaps">
			<p>{gapSummaryText(count, latest)}</p>
			<p className="footnote">{GAP_RECOVERY_NOTE}</p>
			{gaps.length > 0 && (
				<button
					type="button"
					className="text-button"
					aria-expanded={open}
					aria-controls="worker-gaps-detail"
					onClick={() => setOpen(!open)}
				>
					{open ? "收起中断明细" : "展开中断明细"}
					{open ? <CaretUp size={14} /> : <CaretDown size={14} />}
				</button>
			)}
			{open && gaps.length > 0 && <GapDetails count={count} gaps={gaps} />}
		</div>
	);
}

export function BackgroundStatus() {
	const client = useQueryClient();
	const query = useQuery(backgroundStatusQuery(client));
	const gaps = query.data?.recent_gaps ?? [];
	const gapCount = query.data?.gap_count_7d ?? gaps.length;
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
					{(gapCount > 0 || gaps.length > 0) && (
						<WorkerGaps count={gapCount} gaps={gaps} />
					)}
				</>
			)}
		</section>
	);
}
