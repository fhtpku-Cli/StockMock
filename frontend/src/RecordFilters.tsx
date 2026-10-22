export type RecordFilter = {
	readonly from: string;
	readonly to: string;
	readonly market: string;
};
export const initialRecordFilter: RecordFilter = {
	from: "",
	to: "",
	market: "all",
};
export function recordMatches(
	createdAt: string,
	currency: string | undefined,
	filter: RecordFilter,
): boolean {
	const day = new Intl.DateTimeFormat("en-CA", {
		timeZone: "Asia/Shanghai",
		year: "numeric",
		month: "2-digit",
		day: "2-digit",
	}).format(new Date(createdAt));
	const market =
		currency === "CNY"
			? "CN"
			: currency === "HKD"
				? "HK"
				: currency === "USD"
					? "US"
					: "none";
	return (
		(!filter.from || day >= filter.from) &&
		(!filter.to || day <= filter.to) &&
		(filter.market === "all" || market === filter.market)
	);
}
export function RecordFilters({
	value,
	onChange,
}: {
	readonly value: RecordFilter;
	readonly onChange: (value: RecordFilter) => void;
}) {
	return (
		<div className="filters record-filters">
			<label>
				起始日期
				<input
					aria-label="起始日期"
					type="date"
					value={value.from}
					max={value.to || undefined}
					onChange={(e) => onChange({ ...value, from: e.target.value })}
				/>
			</label>
			<label>
				结束日期
				<input
					aria-label="结束日期"
					type="date"
					value={value.to}
					min={value.from || undefined}
					onChange={(e) => onChange({ ...value, to: e.target.value })}
				/>
			</label>
			<label>
				关联市场
				<select
					aria-label="关联市场"
					value={value.market}
					onChange={(e) => onChange({ ...value, market: e.target.value })}
				>
					<option value="all">全部（含未关联）</option>
					<option value="CN">A 股 / 人民币</option>
					<option value="HK">港股 / 港币</option>
					<option value="US">美股 / 美元</option>
				</select>
			</label>
			<button
				type="button"
				className="text-button"
				onClick={() => onChange(initialRecordFilter)}
			>
				清除筛选
			</button>
		</div>
	);
}
