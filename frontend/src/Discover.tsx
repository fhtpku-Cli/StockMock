import { ArrowUpRight, MagnifyingGlass } from "@phosphor-icons/react";
import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { z } from "zod";
import { api, errorMessage } from "./api";
import { DiscoveryResults } from "./DiscoveryResults";
import type { Instrument } from "./schema";
import { instrumentSchema, instrumentSearchSchema } from "./schema";
import { Badge, Empty, ErrorNotice, Loading, Panel } from "./ui";
import { useWatchlist } from "./useWatchlist";

export function Discover({
	onSelect,
}: {
	readonly onSelect: (i: Instrument) => void;
}) {
	const [search, setSearch] = useState("");
	const [query, setQuery] = useState("");
	const [kind, setKind] = useState("all");
	const [availability, setAvailability] = useState("all");
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
	const searching = trimmed.length > 0;
	const waiting = searching && (trimmed !== query || onlineSearch.isPending);
	const active = searching ? onlineSearch : instruments;
	const items = searching ? onlineSearch.data?.items : instruments.data;
	const filtered =
		items?.filter(
			(instrument) =>
				(market === "all" || instrument.market === market) &&
				(kind === "all" || instrument.kind === kind) &&
				(availability === "all" ||
					(availability === "tradable"
						? instrument.tradable
						: !instrument.tradable)),
		) ?? [];
	return (
		<div className="stack discovery">
			<Panel
				title="我的自选"
				action={<Badge>{watch.data?.length ?? "—"} 只标的</Badge>}
			>
				{watch.isError ? (
					<ErrorNotice
						message={errorMessage(watch.error)}
						retry={() => void watch.refetch()}
					/>
				) : watch.isPending ? (
					<Loading />
				) : watch.data.length === 0 ? (
					<Empty title="让观察有一个起点">
						在下方搜索公司或 ETF，点选星标加入自选。
					</Empty>
				) : (
					<div className="watch-grid">
						{watch.data.map((instrument) => (
							<button
								type="button"
								className="watch-card"
								key={instrument.symbol}
								onClick={() => onSelect(instrument)}
							>
								<div>
									<span className="security-avatar">
										{instrument.name.slice(0, 1)}
									</span>
									<ArrowUpRight size={18} />
								</div>
								<strong>{instrument.name}</strong>
								<span>
									{instrument.symbol} · {instrument.market}
								</span>
								<Badge tone={instrument.tradable ? "good" : "warn"}>
									{instrument.tradable ? "可模拟交易" : "仅观察"}
								</Badge>
							</button>
						))}
					</div>
				)}
			</Panel>
			<Panel title="发现市场 · 在线搜索">
				<p className="footnote discovery-intro">
					按名称或代码查询 A 股、港股、美股与
					ETF。目录外证券可加入自选观察，交易规则核验前不开放模拟交易。
				</p>
				<div className="discovery-filters">
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
					<select
						aria-label="筛选市场"
						value={market}
						onChange={(event) => setMarket(event.target.value)}
					>
						<option value="all">全部市场</option>
						<option value="CN">A 股</option>
						<option value="HK">港股</option>
						<option value="US">美股</option>
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
					<select
						aria-label="筛选交易状态"
						value={availability}
						onChange={(event) => setAvailability(event.target.value)}
					>
						<option value="all">全部状态</option>
						<option value="tradable">可模拟交易</option>
						<option value="observe">仅观察</option>
					</select>
				</div>
				{toggle.isError && <ErrorNotice message={errorMessage(toggle.error)} />}
				<div aria-live="polite" aria-busy={waiting || active.isFetching}>
					{waiting || (!searching && instruments.isPending) ? (
						<Loading />
					) : active.isError ? (
						<ErrorNotice
							message={errorMessage(active.error)}
							retry={() => void active.refetch()}
						/>
					) : (
						<>
							<p className="footnote discovery-result-count">
								{searching
									? `在线查询结果 · 当前筛选 ${filtered.length} 只`
									: `常用已接入证券 · 当前筛选 ${filtered.length} 只（非全市场清单）`}
							</p>
							{searching && onlineSearch.data && (
								<p className="footnote">
									{onlineSearch.data.message} · 来源：{onlineSearch.data.source}
								</p>
							)}
							{filtered.length === 0 ? (
								<Empty
									title={
										searching
											? "当前查询结果中没有匹配标的"
											: "常用证券中没有匹配标的"
									}
								>
									试试更完整的证券代码或公司名称，或放宽筛选。搜索结果可能不完整。
								</Empty>
							) : (
								<DiscoveryResults
									items={filtered}
									onSelect={onSelect}
									onToggle={(instrument) => toggle.mutate(instrument)}
									contains={contains}
									pending={pending}
								/>
							)}
						</>
					)}
				</div>
			</Panel>
			<p className="footnote">
				搜索结果不是完整证券清单。可观察不代表可交易；行情时间与来源请在标的详情核对。
			</p>
		</div>
	);
}
