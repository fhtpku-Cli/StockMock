import { MagnifyingGlass } from "@phosphor-icons/react";
import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { z } from "zod";
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

function InstrumentList({
	items,
	onSelect,
	empty,
	...actions
}: {
	readonly items: readonly Instrument[];
	readonly onSelect: (i: Instrument) => void;
	readonly empty: string;
	readonly onToggle: (i: Instrument) => void;
	readonly contains: (symbol: string) => boolean;
	readonly pending: boolean;
}) {
	const [visible, setVisible] = useState(PAGE_SIZE);
	return items.length === 0 ? (
		<Empty title={empty}>可调整筛选，或在在线搜索中查找目录外的证券。</Empty>
	) : (
		<>
			<DiscoveryResults
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
}: {
	readonly onSelect: (i: Instrument) => void;
}) {
	const [localSearch, setLocalSearch] = useState("");
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
		queryFn: () =>
			api(
				`instruments/search?q=${encodeURIComponent(query)}`,
				instrumentSearchSchema,
			),
		enabled: query.length > 0 && query === trimmed,
		retry: false,
		staleTime: 60_000,
	});
	const enabled = instruments.data?.filter((item) => item.tradable) ?? [];
	const filtered = enabled.filter(
		(item) =>
			(market === "all" || item.market === market) &&
			(kind === "all" || item.kind === kind) &&
			`${item.symbol} ${item.name}`
				.toLocaleLowerCase()
				.includes(localSearch.trim().toLocaleLowerCase()),
	);
	const listActions = {
		onSelect,
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
				title="已可交易"
				action={
					<Badge tone="good">
						{instruments.data ? enabled.length : "—"} 只已启用
					</Badge>
				}
			>
				<p className="footnote discovery-intro">
					这些标的已通过交易规则核验，可进入详情练习；能否下单仍取决于当前行情、交易时间和可用资金。港股继续仅观察。
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
							aria-label="筛选已可交易证券"
							placeholder="在已可交易中按名称或代码筛选"
							value={localSearch}
							onChange={(event) => setLocalSearch(event.target.value)}
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
				{instruments.isPending ? (
					<Loading />
				) : (
					instruments.data && (
						<InstrumentList
							key={`${market}-${kind}-${localSearch}`}
							items={filtered}
							empty="已可交易目录中没有匹配项"
							{...listActions}
						/>
					)
				)}
			</Panel>
			<Panel title="在线搜索">
				<p className="footnote discovery-intro">
					查找目录之外的证券。查到不代表已可交易；可在详情核验启用，失败原因会保留显示。在线服务失败不会移除已加载的自选和目录。
				</p>
				<label className="search-box">
					<MagnifyingGlass size={20} />
					<input
						aria-label="搜索代码或名称"
						placeholder="输入名称或代码，在线查询"
						value={search}
						maxLength={80}
						onChange={(event) => setSearch(event.target.value)}
					/>
				</label>
				<div aria-live="polite" aria-busy={waiting || onlineSearch.isFetching}>
					{!trimmed ? (
						<p className="footnote">
							输入关键词后查询在线来源，结果可能不完整。
						</p>
					) : waiting ? (
						<Loading />
					) : onlineSearch.isError ? (
						<ErrorNotice
							message={`在线结果当前不可用：${errorMessage(onlineSearch.error)} 已加载的自选和已可交易目录仍可查看。`}
							retry={() => void onlineSearch.refetch()}
						/>
					) : (
						onlineSearch.data && (
							<>
								<p className="footnote">
									{onlineSearch.data.message} · 来源：{onlineSearch.data.source}
								</p>
								<InstrumentList
									key={query}
									items={onlineSearch.data.items}
									empty="当前在线查询没有匹配标的"
									{...listActions}
								/>
							</>
						)
					)}
				</div>
			</Panel>
		</div>
	);
}
