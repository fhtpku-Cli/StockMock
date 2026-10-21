import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
	MagnifyingGlass,
	Plus,
	Star,
	ArrowUpRight,
	Minus,
} from "@phosphor-icons/react";
import { z } from "zod";
import { api, errorMessage, removeWatch } from "./api";
import { acknowledgmentSchema, instrumentSchema } from "./schema";
import type { Instrument } from "./schema";
import { Badge, Empty, ErrorNotice, Loading, Panel } from "./ui";
export function Discover({
	onSelect,
}: {
	readonly onSelect: (i: Instrument) => void;
}) {
	const [search, setSearch] = useState("");
	const [market, setMarket] = useState("all");
	const client = useQueryClient();
	const instruments = useQuery({
		queryKey: ["instruments"],
		queryFn: () => api("instruments", z.array(instrumentSchema)),
	});
	const watch = useQuery({
		queryKey: ["watchlist"],
		queryFn: () => api("watchlist", z.array(instrumentSchema)),
	});
	const mutate = useMutation({
		mutationFn: async (i: Instrument) => {
			if (watch.data?.some((w) => w.symbol === i.symbol))
				await removeWatch(i.symbol);
			else await api("watchlist", acknowledgmentSchema, { symbol: i.symbol });
		},
		onSuccess: () => client.invalidateQueries({ queryKey: ["watchlist"] }),
	});
	const filtered = instruments.data?.filter(
		(i) =>
			(market === "all" || i.market === market) &&
			`${i.symbol} ${i.name}`.toLowerCase().includes(search.toLowerCase()),
	);
	return (
		<div className="stack">
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
						在下方寻找熟悉的公司或 ETF，点选星标加入自选。
					</Empty>
				) : (
					<div className="watch-grid">
						{watch.data.map((i) => (
							<button
								type="button"
								className="watch-card"
								key={i.symbol}
								onClick={() => onSelect(i)}
							>
								<div>
									<span className="security-avatar">{i.name.slice(0, 1)}</span>
									<ArrowUpRight size={18} />
								</div>
								<strong>{i.name}</strong>
								<span>
									{i.symbol} · {i.market}
								</span>
								<Badge tone={i.tradable ? "good" : "warn"}>
									{i.tradable ? "可模拟交易" : "仅观察"}
								</Badge>
							</button>
						))}
					</div>
				)}
			</Panel>
			<Panel title="发现市场">
				<div className="filters">
					<label className="search-box">
						<MagnifyingGlass size={20} />
						<input
							aria-label="搜索代码或名称"
							placeholder="搜索代码或名称"
							value={search}
							onChange={(e) => setSearch(e.target.value)}
						/>
					</label>
					<select
						aria-label="筛选市场"
						value={market}
						onChange={(e) => setMarket(e.target.value)}
					>
						<option value="all">全部市场</option>
						{[...new Set(instruments.data?.map((i) => i.market))].map((m) => (
							<option key={m} value={m}>
								{m}
							</option>
						))}
					</select>
				</div>
				{mutate.isError && <ErrorNotice message={errorMessage(mutate.error)} />}
				{instruments.isPending ? (
					<Loading />
				) : instruments.isError ? (
					<ErrorNotice
						message={errorMessage(instruments.error)}
						retry={() => void instruments.refetch()}
					/>
				) : filtered?.length === 0 ? (
					<Empty title="没有找到匹配的标的">
						试试证券代码，或切换市场。首版仅展示已接入的证券。
					</Empty>
				) : (
					<div className="security-list">
						{filtered?.map((i) => (
							<div className="security-row" key={i.symbol}>
								<button
									type="button"
									className="security-main"
									onClick={() => onSelect(i)}
								>
									<span className="security-avatar">{i.name.slice(0, 1)}</span>
									<span>
										<strong>{i.name}</strong>
										<small>
											{i.symbol} · {i.market} ·{" "}
											{i.kind === "ETF" ? "ETF" : "股票"}
										</small>
									</span>
								</button>
								<Badge tone={i.tradable ? "good" : "warn"}>
									{i.tradable ? "可交易" : "仅观察"}
								</Badge>
								<button
									type="button"
									className="icon-button"
									aria-label={`${watch.data?.some((w) => w.symbol === i.symbol) ? "移除" : "添加"}自选 ${i.name}`}
									disabled={
										mutate.isPending || watch.isError || watch.isPending
									}
									onClick={() => mutate.mutate(i)}
								>
									{watch.data?.some((w) => w.symbol === i.symbol) ? (
										<Star weight="fill" className="star" size={20} />
									) : (
										<Plus size={20} />
									)}
								</button>
							</div>
						))}
					</div>
				)}
			</Panel>
			<p className="footnote">
				搜索结果不是完整证券清单。可观察不代表可交易；行情时间与来源请在标的详情核对。
			</p>
		</div>
	);
}
