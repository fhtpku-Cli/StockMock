import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useId, useState } from "react";
import { dateRangeError, filterParams } from "./analysis";
import { ApiError, api, errorMessage } from "./api";
import { CashReconciliation } from "./CashReconciliation";
import { CsvDownload } from "./CsvDownload";
import { LedgerEntries } from "./LedgerEntries";
import {
	cashStatementSchema,
	ledgerKinds,
	ledgerKindValue,
	ledgerPageSchema,
} from "./statementSchema";
import { ErrorNotice, Loading, Panel } from "./ui";

const initialFilter = { start_date: "", end_date: "", currency: "", kind: "" };
export function CashStatement({ roundId }: { readonly roundId?: string }) {
	const id = useId();
	const client = useQueryClient();
	const [draft, setDraft] = useState(initialFilter);
	const [filter, setFilter] = useState(initialFilter);
	const [validation, setValidation] = useState<string | null>(null);
	const [cursor, setCursor] = useState<{
		readonly offset: number;
		readonly boundary?: number;
		readonly round?: string;
	}>({ offset: 0 });
	const prefix = roundId ? `rounds/${encodeURIComponent(roundId)}/` : "";
	const params = filterParams(filter);
	const query = useQuery({
		queryKey: ["ledger", "page", roundId, params, cursor],
		queryFn: () =>
			api(
				`${prefix}${roundId ? "ledger" : "ledger/page"}?${params}&offset=${cursor.offset}&limit=50${cursor.boundary === undefined ? "" : `&boundary=${cursor.boundary}`}`,
				ledgerPageSchema,
			),
	});
	const statement = useQuery({
		queryKey: ["ledger", "statement", roundId, params],
		queryFn: () =>
			api(`${prefix}cash-statement?${params}`, cashStatementSchema),
	});
	const roundChanged =
		(cursor.boundary !== undefined &&
			query.error instanceof ApiError &&
			query.error.code === "LEDGER_CHANGED") ||
		Boolean(
			cursor.round &&
				((query.data && query.data.round_id !== cursor.round) ||
					(statement.data && statement.data.round_id !== cursor.round)),
		);
	useEffect(() => {
		if (roundChanged) {
			void client.invalidateQueries({ queryKey: ["ledger", "page", roundId] });
			setCursor({ offset: 0 });
			void statement.refetch();
		}
	}, [roundChanged, statement.refetch, client, roundId]);
	const refresh = () => {
		void client.invalidateQueries({ queryKey: ["ledger", "page", roundId] });
		setCursor({ offset: 0 });
		void statement.refetch();
	};
	return (
		<Panel
			title={roundId ? "历史资金核对与流水" : "资金核对与流水"}
			className="cash-statement"
		>
			<form
				onSubmit={(event) => {
					event.preventDefault();
					const error = dateRangeError(draft);
					setValidation(error);
					if (!error) {
						setFilter({ ...draft, kind: ledgerKindValue(draft.kind) });
						setCursor({ offset: 0 });
					}
				}}
			>
				<div className="analysis-filters">
					<label>
						资金开始日期
						<input
							type="date"
							value={draft.start_date}
							onChange={(event) =>
								setDraft({ ...draft, start_date: event.target.value })
							}
						/>
					</label>
					<label>
						资金结束日期
						<input
							type="date"
							value={draft.end_date}
							onChange={(event) =>
								setDraft({ ...draft, end_date: event.target.value })
							}
						/>
					</label>
					<label>
						资金币种
						<select
							value={draft.currency}
							onChange={(event) =>
								setDraft({ ...draft, currency: event.target.value })
							}
						>
							<option value="">全部币种 · 分别核对</option>
							{["CNY", "USD", "HKD"].map((value) => (
								<option key={value}>{value}</option>
							))}
						</select>
					</label>
					<label>
						流水类型
						<input
							list={`${id}-kinds`}
							value={draft.kind}
							placeholder="全部类型"
							onChange={(event) =>
								setDraft({ ...draft, kind: event.target.value })
							}
						/>
						<datalist id={`${id}-kinds`}>
							{[
								...new Set([
									...Object.keys(ledgerKinds),
									...(query.data?.items.map((row) => row.kind) ?? []),
								]),
							].map((kind) => (
								<option key={kind} value={ledgerKinds[kind] ?? kind}>
									{kind}
								</option>
							))}
						</datalist>
					</label>
				</div>
				<div className="analysis-actions">
					<button type="submit" className="button">
						应用资金筛选
					</button>
					<button
						type="button"
						className="text-button"
						onClick={() => {
							setDraft(initialFilter);
							setFilter(initialFilter);
							setValidation(null);
							setCursor({ offset: 0 });
						}}
					>
						清除筛选
					</button>
					<button type="button" className="text-button" onClick={refresh}>
						刷新到最新流水
					</button>
				</div>
				{validation && <ErrorNotice message={validation} />}
			</form>
			<p className="footnote">
				当前列表：{filter.start_date || "本轮开始"} 至{" "}
				{filter.end_date || "当前"} · {filter.currency || "各币种"} ·{" "}
				{ledgerKinds[filter.kind] ?? (filter.kind || "全部类型")}
				。修改条件后点击“应用资金筛选”。
			</p>
			{statement.isPending ? (
				<Loading />
			) : statement.isError ? (
				<ErrorNotice
					message={errorMessage(statement.error)}
					retry={() => void statement.refetch()}
				/>
			) : (
				<CashReconciliation statement={statement.data} />
			)}
			<div className="analysis-actions">
				<CsvDownload
					path={`${prefix}ledger/export.csv?${params}`}
					name={`资金筛选-${roundId ?? "当前轮"}-${filter.currency || "各币种"}.csv`}
				>
					导出筛选流水
				</CsvDownload>
				<CsvDownload
					path={`${prefix}export.csv`}
					name={`完整资金流水-${roundId ?? "当前轮"}.csv`}
				>
					导出本轮完整流水
				</CsvDownload>
			</div>
			<p className="footnote">
				导出包含当次读取的全部筛选结果，不限于当前页。翻页期间新增流水在刷新后显示。
			</p>
			{query.isPending || roundChanged ? (
				<Loading />
			) : query.isError ? (
				<ErrorNotice message={errorMessage(query.error)} retry={refresh} />
			) : (
				<>
					<p className="footnote" role="status">
						共 {query.data.total} 条 · 第{" "}
						{Math.floor(query.data.offset / 50) + 1} 页
					</p>
					{query.data.items.length === 0 ? (
						<p className="chart-empty">
							此页没有符合条件的流水；可调整条件或刷新回第一页。
						</p>
					) : (
						<LedgerEntries
							rows={query.data.items}
							{...(roundId ? { roundId } : {})}
						/>
					)}
					<div className="analysis-actions">
						<button
							type="button"
							className="button"
							disabled={cursor.offset === 0 || query.isFetching}
							onClick={() =>
								setCursor({
									offset: Math.max(0, cursor.offset - 50),
									boundary: query.data.boundary,
									round: query.data.round_id,
								})
							}
						>
							上一页流水
						</button>
						<button
							type="button"
							className="button"
							disabled={
								query.data.offset + query.data.items.length >=
									query.data.total || query.isFetching
							}
							onClick={() =>
								setCursor({
									offset: cursor.offset + 50,
									boundary: query.data.boundary,
									round: query.data.round_id,
								})
							}
						>
							下一页流水
						</button>
					</div>
				</>
			)}
		</Panel>
	);
}
