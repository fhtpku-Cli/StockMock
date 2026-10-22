import {
	useInfiniteQuery,
	useMutation,
	useQueryClient,
} from "@tanstack/react-query";
import { useState } from "react";
import { api, errorMessage, time } from "./api";
import { Pagination } from "./Pagination";
import { alertPageSchema, alertSchema } from "./planSchema";
import { Badge, ErrorNotice, Loading, Panel } from "./ui";
import { useOnline } from "./useOnline";

export function Alerts({
	symbol,
	roundId,
	onSymbol,
	compact = false,
}: {
	readonly compact?: boolean;
	readonly symbol?: string;
	readonly roundId?: string;
	readonly onSymbol?: (symbol: string) => void;
}) {
	const [unreadOnly, setUnreadOnly] = useState(!roundId);
	const online = useOnline();
	const client = useQueryClient();
	const params = new URLSearchParams({ unread_only: String(unreadOnly) });
	if (symbol) params.set("symbol", symbol);
	if (roundId) params.set("round_id", roundId);
	const query = useInfiniteQuery({
		queryKey: [
			"alerts",
			symbol ?? "all",
			roundId ?? "current",
			unreadOnly,
			compact,
		],
		initialPageParam: 0,
		queryFn: ({ pageParam }) =>
			api(
				`alerts?${params}&offset=${pageParam}&limit=${compact ? 5 : 10}`,
				alertPageSchema,
			),
		getNextPageParam: (last) =>
			last.offset + last.items.length < last.total
				? last.offset + last.items.length
				: undefined,
		refetchInterval: roundId ? false : 30_000,
	});
	const read = useMutation({
		mutationFn: (id: string) =>
			api(`alerts/${encodeURIComponent(id)}/read`, alertSchema, {}),
		onSuccess: () => client.invalidateQueries({ queryKey: ["alerts"] }),
	});
	const items = query.data?.pages.flatMap((page) => page.items) ?? [];
	return (
		<Panel
			title={roundId ? "历史站内提示" : "计划提示"}
			className="alerts-panel"
			action={<Badge>{query.data?.pages[0]?.unread_count ?? "—"} 条未读</Badge>}
		>
			<a
				className="text-button"
				href={`/api/alerts/export.csv${roundId ? `?round_id=${encodeURIComponent(roundId)}` : ""}`}
				download
			>
				导出提示
			</a>
			<div className="button-row alert-filter">
				<button
					type="button"
					className={unreadOnly ? "button small primary" : "button small"}
					aria-pressed={unreadOnly}
					onClick={() => setUnreadOnly(true)}
				>
					未读
				</button>
				<button
					type="button"
					className={!unreadOnly ? "button small primary" : "button small"}
					aria-pressed={!unreadOnly}
					onClick={() => setUnreadOnly(false)}
				>
					全部提示
				</button>
			</div>
			{query.isPending ? (
				<Loading />
			) : (
				query.isError && (
					<ErrorNotice
						message={errorMessage(query.error)}
						retry={() => void query.refetch()}
					/>
				)
			)}
			{read.isError && <ErrorNotice message={errorMessage(read.error)} />}
			{query.isSuccess && items.length === 0 && (
				<p className="footnote">
					{unreadOnly ? "暂无未读提示。" : "尚无提示记录。"}
					进行中的计划到复盘日或观察到有效到价样本后，会出现在这里。
				</p>
			)}
			<div className="alert-list">
				{items.map((item) => (
					<article key={item.id}>
						<div className="plan-heading">
							{onSymbol ? (
								<button
									type="button"
									className="symbol-link"
									onClick={() => onSymbol(item.symbol)}
								>
									{item.symbol}
								</button>
							) : (
								<strong>{item.symbol}</strong>
							)}
							<Badge tone={item.read_at ? "neutral" : "warn"}>
								{item.read_at ? "已读" : "未读"}
							</Badge>
						</div>
						<p>{item.message}</p>
						{item.market_time && (
							<p className="footnote">
								观察样本 {item.price ?? "未记录"} · 市场时间{" "}
								{time(item.market_time)}（北京时间） ·{" "}
								{item.source || "来源未记录"}
							</p>
						)}
						<p className="footnote">
							记录于 {time(item.created_at)}（北京时间） · 不会自动买卖或撤单
						</p>
						{!roundId && !item.read_at && (
							<button
								type="button"
								className="button small"
								disabled={!online || read.isPending}
								onClick={() => read.mutate(item.id)}
							>
								确认已读
							</button>
						)}
					</article>
				))}
			</div>
			{query.data && (
				<Pagination
					count={items.length}
					total={query.data.pages[0]?.total ?? 0}
					pending={query.isFetchingNextPage}
					more={query.hasNextPage}
					onMore={() => void query.fetchNextPage()}
				/>
			)}
		</Panel>
	);
}
