import { MagnifyingGlass } from "@phosphor-icons/react";
import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { z } from "zod";
import { marketName } from "./analysis";
import { api, errorMessage } from "./api";
import { DiscoveryResults } from "./DiscoveryResults";
import type { Instrument } from "./schema";
import { instrumentSchema, instrumentSearchSchema } from "./schema";
import { Badge, Empty, ErrorNotice, Loading, Panel } from "./ui";
import { useWatchlist } from "./useWatchlist";

const PAGE_SIZE = 24;
const coverageGroups = [
	{ market: "CN", kind: "stock", label: "A 股" },
	{ market: "CN", kind: "ETF", label: "境内 ETF" },
	{ market: "US", kind: "stock", label: "美股" },
	{ market: "US", kind: "ETF", label: "美股 ETF" },
] as const;

export function localSearchCandidates(
	catalog: readonly Instrument[],
	query: string,
	market: string,
) {
	const keyword = query.trim().toLowerCase();
	if (keyword)
		return catalog.filter((item) =>
			`${item.symbol} ${item.name}`.toLowerCase().includes(keyword),
		);
	return catalog.filter((item) =>
		market === "HK" ? item.market === "HK" : item.tradable,
	);
}

export function InstrumentList({
	items,
	onSelect,
	empty,
	...actions
}: {
	readonly items: readonly Instrument[];
	readonly onSelect: (i: Instrument) => void;
	readonly empty: string;
	readonly onBuy: (i: Instrument) => void;
	readonly onToggle: (i: Instrument) => void;
	readonly contains: (symbol: string) => boolean;
	readonly pending: boolean;
}) {
	const [visible, setVisible] = useState(PAGE_SIZE);
	const ruleCounts = new Map<string, Map<string, number>>();
	for (const item of items) {
		if (!item.tradable || !item.restriction) continue;
		const counts = ruleCounts.get(item.market) ?? new Map<string, number>();
		counts.set(item.restriction, (counts.get(item.restriction) ?? 0) + 1);
		ruleCounts.set(item.market, counts);
	}
	const sharedRules = new Map<string, string>();
	for (const [market, counts] of ruleCounts) {
		const mostCommon = Array.from(counts).sort((a, b) => b[1] - a[1])[0];
		if (mostCommon && mostCommon[1] > 1) sharedRules.set(market, mostCommon[0]);
	}
	return items.length === 0 ? (
		<Empty title={empty}>可输入证券代码或调整筛选。</Empty>
	) : (
		<>
			{sharedRules.size > 0 && (
				<details className="reading-note">
					<summary>本列表可交易标的的共同规则</summary>
					{Array.from(sharedRules, ([market, rule]) => (
						<p key={market} className="footnote">
							{marketName(market)}（除行内另注外） · {rule}
						</p>
					))}
				</details>
			)}
			<DiscoveryResults
				sharedRules={sharedRules}
				items={items.slice(0, visible)}
				onSelect={onSelect}
				{...actions}
			/>
			<div className="list-pagination">
				<span className="footnote">
					已显示 {Math.min(visible, items.length)} / {items.length} 只
				</span>
				{visible < items.length && (
					<button
						className="button small"
						type="button"
						onClick={() => setVisible((count) => count + PAGE_SIZE)}
					>
						再显示 {Math.min(PAGE_SIZE, items.length - visible)} 只
					</button>
				)}
			</div>
		</>
	);
}

export function Discover({
	onSelect,
	onBuy,
}: {
	readonly onSelect: (i: Instrument) => void;
	readonly onBuy: (i: Instrument) => void;
}) {
	const [search, setSearch] = useState("");
	const [query, setQuery] = useState("");
	const [kind, setKind] = useState("all");
	const [market, setMarket] = useState("all");
	const { watch, toggle, contains, pending } = useWatchlist();
	const trimmed = search.trim();
	useEffect(() => {
		const timer = window.setTimeout(() => setQuery(trimmed), 350);
		return () => window.clearTimeout(timer);
	}, [trimmed]);
	const instruments = useQuery({
		queryKey: ["instruments"],
		queryFn: () => api("instruments", z.array(instrumentSchema)),
	});
	const onlineSearch = useQuery({
		queryKey: ["instrument-search", query],
		queryFn: ({ signal }) =>
			api(
				`instruments/search?q=${encodeURIComponent(query)}`,
				instrumentSearchSchema,
				undefined,
				{ signal },
			),
		enabled: query.length > 0 && query === trimmed,
		retry: false,
		staleTime: 60_000,
	});
	const enabled = instruments.data?.filter((item) => item.tradable) ?? [];
	const identity = (symbol: string) => symbol.replace(/^0+(?=\d+\.HK$)/, "");
	const local = new Map(
		(instruments.data ?? []).map((i) => [identity(i.symbol), i]),
	);
	const matches = localSearchCandidates(
		instruments.data ?? [],
		trimmed,
		market,
	);
	const merged = new Map(matches.map((i) => [identity(i.symbol), i]));
	if (trimmed && trimmed === query)
		for (const item of onlineSearch.data?.items ?? []) {
			const code = identity(item.symbol);
			merged.set(code, local.get(code) ?? item);
		}
	const filtered = Array.from(merged.values()).filter(
		(item) =>
			(market === "all" || item.market === market) &&
			(kind === "all" || item.kind === kind),
	);
	const listActions = {
		onSelect,
		onBuy,
		onToggle: (item: Instrument) => toggle.mutate(item),
		contains,
		pending,
	};
	const waiting =
		trimmed.length > 0 && (trimmed !== query || onlineSearch.isPending);
	return (
		<div className="stack discovery">
			{toggle.isError && <ErrorNotice message={errorMessage(toggle.error)} />}
			<Panel
				title="我的自选"
				action={<Badge>{watch.data?.length ?? "—"} 只标的</Badge>}
			>
				{watch.isError && (
					<ErrorNotice
						message={errorMessage(watch.error)}
						retry={() => void watch.refetch()}
					/>
				)}
				{watch.isPending ? (
					<Loading />
				) : (
					watch.data && (
						<InstrumentList
							items={watch.data}
							empty="自选还是空的"
							{...listActions}
						/>
					)
				)}
			</Panel>
			<Panel
				title="证券搜索"
				action={
					<Badge tone="good">
						{instruments.data ? enabled.length : "—"} 只已启用
					</Badge>
				}
			>
				<p className="footnote discovery-intro">
					输入代码或名称可搜索目录内外的证券。首次买入 A
					股或美股时自动核验，港股仅供观察。
				</p>
				<section className="coverage-summary" aria-label="已启用数量汇总">
					{coverageGroups.map((group) => (
						<div key={`${group.market}-${group.kind}`}>
							<span>{group.label}</span>
							<strong>
								{instruments.data
									? enabled.filter(
											(item) =>
												item.market === group.market &&
												item.kind === group.kind,
										).length
									: "—"}
							</strong>
						</div>
					))}
				</section>
				<div className="discovery-filters">
					<label className="search-box">
						<MagnifyingGlass size={20} />
						<input
							aria-label="搜索代码或名称"
							placeholder="输入证券代码或名称"
							maxLength={80}
							value={search}
							onChange={(event) => setSearch(event.target.value)}
						/>
					</label>
					<select
						aria-label="筛选市场"
						value={market}
						onChange={(event) => setMarket(event.target.value)}
					>
						<option value="all">全部市场</option>
						<option value="CN">A 股及境内 ETF</option>
						<option value="US">美股及 ETF</option>
						<option value="HK">港股（仅观察）</option>
					</select>
					<select
						aria-label="筛选证券类型"
						value={kind}
						onChange={(event) => setKind(event.target.value)}
					>
						<option value="all">全部类型</option>
						<option value="stock">股票</option>
						<option value="ETF">ETF</option>
					</select>
				</div>
				{instruments.isError && (
					<ErrorNotice
						message={errorMessage(instruments.error)}
						retry={() => void instruments.refetch()}
					/>
				)}
				<div aria-live="polite">
					{(instruments.isPending || waiting) && <Loading />}
					{trimmed && trimmed === query && onlineSearch.isError && (
						<ErrorNotice
							message={`在线搜索暂不可用：${errorMessage(onlineSearch.error)} 仍可使用已加载的本地结果。`}
							retry={() => void onlineSearch.refetch()}
						/>
					)}
					{trimmed && trimmed === query && onlineSearch.isSuccess && (
						<p className="footnote">
							在线结果可能不完整，可输入准确代码缩小范围。来源：
							{onlineSearch.data.source}
						</p>
					)}
					{(filtered.length > 0 || (!waiting && !instruments.isPending)) && (
						<InstrumentList
							key={JSON.stringify([market, kind, trimmed])}
							items={filtered}
							empty="没有匹配的证券"
							{...listActions}
						/>
					)}
				</div>
			</Panel>
		</div>
	);
}
