export type ReviewFilter = {
	readonly symbol: string;
	readonly market: string;
	readonly planStatus: string;
	readonly from: string;
	readonly to: string;
};
export const initialReviewFilter: ReviewFilter = {
	symbol: "",
	market: "",
	planStatus: "all",
	from: "",
	to: "",
};
export function filterParams(filter: ReviewFilter, roundId?: string) {
	const params = new URLSearchParams();
	if (roundId) params.set("round_id", roundId);
	if (filter.symbol.trim()) params.set("symbol", filter.symbol.trim());
	if (filter.market) params.set("market", filter.market);
	if (filter.planStatus !== "all") params.set("plan_status", filter.planStatus);
	if (filter.from) params.set("start_date", filter.from);
	if (filter.to) params.set("end_date", filter.to);
	return params;
}
export function ReviewFilters({
	value,
	onChange,
	onApply,
	notes = false,
}: {
	readonly value: ReviewFilter;
	readonly onChange: (value: ReviewFilter) => void;
	readonly onApply: () => void;
	readonly notes?: boolean;
}) {
	const prefix = notes ? "笔记" : "复盘";
	return (
		<form
			className="review-filters"
			onSubmit={(event) => {
				event.preventDefault();
				onApply();
			}}
		>
			<label>
				{prefix}证券代码
				<input
					aria-label={`${prefix}证券代码`}
					value={value.symbol}
					placeholder="本轮证券代码，可输入部分代码"
					onChange={(event) =>
						onChange({ ...value, symbol: event.target.value })
					}
				/>
			</label>
			<label>
				{prefix}市场
				<select
					aria-label={`${prefix}市场`}
					value={value.market}
					onChange={(event) =>
						onChange({ ...value, market: event.target.value })
					}
				>
					<option value="">全部（含未知）</option>
					<option value="CN">A 股</option>
					<option value="US">美股</option>
					<option value="HK">港股</option>
				</select>
			</label>
			{!notes && (
				<label>
					计划状态
					<select
						aria-label="复盘计划状态"
						value={value.planStatus}
						onChange={(event) =>
							onChange({ ...value, planStatus: event.target.value })
						}
					>
						<option value="all">全部</option>
						<option value="draft">草稿</option>
						<option value="active">进行中</option>
						<option value="ended">已结束</option>
						<option value="legacy">无计划旧委托</option>
					</select>
				</label>
			)}
			<label>
				{notes ? "笔记日期" : "最近记录日期"} · 起始
				<input
					aria-label={`${prefix}起始日期`}
					type="date"
					value={value.from}
					onChange={(event) => onChange({ ...value, from: event.target.value })}
				/>
			</label>
			<label>
				{notes ? "笔记日期" : "最近记录日期"} · 结束
				<input
					aria-label={`${prefix}结束日期`}
					type="date"
					value={value.to}
					onChange={(event) => onChange({ ...value, to: event.target.value })}
				/>
			</label>
			<button className="button" type="submit">
				应用{prefix}筛选
			</button>
		</form>
	);
}
