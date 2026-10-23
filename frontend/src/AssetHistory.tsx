import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { dateRangeError, filterParams } from "./analysis";
import { api, errorMessage, money, time } from "./api";
import { CsvDownload } from "./CsvDownload";
import { snapshotRangeSchema } from "./history";
import { SnapshotChart } from "./SnapshotChart";
import { ErrorNotice, Loading, Panel } from "./ui";

const ranges = [
	{ value: "7d", label: "近7天" },
	{ value: "30d", label: "近30天" },
	{ value: "90d", label: "近90天" },
	{ value: "all", label: "本轮全部" },
	{ value: "custom", label: "自定日期" },
] as const;
export function AssetHistory({ roundId }: { readonly roundId?: string }) {
	const [choice, setChoice] = useState("all");
	const [dates, setDates] = useState({ start_date: "", end_date: "" });
	const [params, setParams] = useState("range=all");
	const [validation, setValidation] = useState<string | null>(null);
	const prefix = roundId ? `rounds/${encodeURIComponent(roundId)}/` : "";
	const query = useQuery({
		queryKey: ["snapshots", roundId ?? "current", params],
		queryFn: () =>
			api(`${prefix}snapshots/range?${params}`, snapshotRangeSchema),
	});
	return (
		<Panel title="资产轨迹" className="asset-history">
			<fieldset className="analysis-actions" aria-label="资产轨迹范围">
				{ranges.map((range) => (
					<button
						type="button"
						className={choice === range.value ? "button primary" : "button"}
						aria-pressed={choice === range.value}
						key={range.value}
						onClick={() => {
							setChoice(range.value);
							setValidation(null);
							if (range.value !== "custom") setParams(`range=${range.value}`);
						}}
					>
						{range.label}
					</button>
				))}
			</fieldset>
			{choice === "custom" && (
				<form
					onSubmit={(event) => {
						event.preventDefault();
						const error =
							dateRangeError(dates) ??
							(!dates.start_date || !dates.end_date
								? "请选择开始和结束日期。"
								: null);
						setValidation(error);
						if (!error) setParams(filterParams({ range: "custom", ...dates }));
					}}
				>
					<div className="analysis-filters">
						<label>
							轨迹开始日期
							<input
								type="date"
								required
								value={dates.start_date}
								onChange={(event) =>
									setDates({ ...dates, start_date: event.target.value })
								}
							/>
						</label>
						<label>
							轨迹结束日期
							<input
								type="date"
								required
								value={dates.end_date}
								onChange={(event) =>
									setDates({ ...dates, end_date: event.target.value })
								}
							/>
						</label>
					</div>
					<button type="submit" className="button">
						应用轨迹日期
					</button>
					{validation && <ErrorNotice message={validation} />}
				</form>
			)}
			<p className="footnote">
				北京时间自然日。
				{roundId ? "快捷范围截至本轮归档日。" : "快捷范围截至今天。"}
				只展示后台实际留存的记录，不补画历史；采集时资产不是收盘净值。
			</p>
			{query.isPending ? (
				<Loading />
			) : query.isError ? (
				<ErrorNotice
					message={errorMessage(query.error)}
					retry={() => void query.refetch()}
				/>
			) : (
				<>
					<p className="footnote">
						当前显示：{query.data.start_date ?? "本轮开始"} 至{" "}
						{query.data.end_date ?? "本轮结束"}
					</p>
					<div className="history-summary">
						<span>已记录首末资产变化</span>
						<strong>
							{query.data.change_cny !== null &&
							Number(query.data.change_cny) > 0
								? "+"
								: ""}
							{money(query.data.change_cny)}
						</strong>
						<p>{query.data.reason}</p>
						<small>
							实际记录 {query.data.recorded_days} 天 · 完整估值{" "}
							{query.data.complete_days} 天 · 未记录 {query.data.missing_days}{" "}
							天
						</small>
						<small>
							实际首记录：
							{query.data.first
								? `${query.data.first.day} · ${time(query.data.first.captured_at)}`
								: "无记录"}
						</small>
						<small>
							实际末记录：
							{query.data.last
								? `${query.data.last.day} · ${time(query.data.last.captured_at)}`
								: "无记录"}
						</small>
					</div>
					{!query.data.continuous && query.data.recorded_days > 0 && (
						<p className="notice">
							覆盖不连续：未记录日期、缺失或过时估值均不连线。有效首末记录的差额不代表逐日或收盘收益。
						</p>
					)}
					{query.data.items.length === 0 ? (
						<p className="chart-empty">
							所选范围没有实际资产记录。可调整日期；不会补采过去的数据。
						</p>
					) : (
						<SnapshotChart items={query.data.items} />
					)}
					<div className="analysis-actions">
						<CsvDownload
							path={`${prefix}snapshots/export.csv?${params}`}
							name={`资产快照-${roundId ?? "当前轮"}-所选范围.csv`}
						>
							导出所选快照
						</CsvDownload>
						<CsvDownload
							path={`${prefix}snapshots/export.csv?range=all`}
							name={`资产快照-${roundId ?? "当前轮"}-全部.csv`}
						>
							导出本轮全部快照
						</CsvDownload>
					</div>
				</>
			)}
		</Panel>
	);
}
