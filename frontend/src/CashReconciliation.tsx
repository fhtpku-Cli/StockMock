import type { z } from "zod";
import { money } from "./api";
import type { cashStatementSchema } from "./statementSchema";
import { Badge } from "./ui";
export function CashReconciliation({
	statement,
}: {
	readonly statement: z.infer<typeof cashStatementSchema>;
}) {
	return (
		<div className="cash-reconciliation">
			<p className="footnote">
				日期范围：{statement.start_date ?? "本轮开始"} 至{" "}
				{statement.end_date ?? "当前"}
				（北京时间）。期初、期末和核对均包含全部类型，不随流水类型筛选改变。换汇分币种记录，不是投资收益。
			</p>
			<div className="analysis-metrics">
				{statement.currencies.map((row) => (
					<section
						className="analysis-metric"
						key={row.currency}
						data-cash-currency={row.currency}
					>
						<h3>
							{row.currency} 现金核对{" "}
							<Badge tone={Number(row.difference) === 0 ? "good" : "warn"}>
								{Number(row.difference) === 0 ? "一致" : "待核对"}
							</Badge>
						</h3>
						<dl className="summary-list">
							{[
								["期初余额", row.opening_balance],
								["期间流入", row.period_inflow],
								["期间流出", row.period_outflow],
								["期间净变化", row.period_net_change],
								["期末余额", row.closing_balance],
								["所选类型净变化", row.selected_net_change],
							].map(([label, value]) => (
								<div key={label}>
									<dt>{label}</dt>
									<dd>
										{label?.includes("净变化") && Number(value) > 0 ? "+" : ""}
										{money(value ?? null, row.currency)}
									</dd>
								</div>
							))}
						</dl>
						<details>
							<summary>查看本轮累计核对</summary>
							<dl className="summary-list">
								{[
									["初始现金", row.initial_cash],
									["累计流入", row.total_inflow],
									["累计流出", row.total_outflow],
									["计算余额", row.calculated_balance],
									["账本余额", row.ledger_balance],
									["差额", row.difference],
								].map(([label, value]) => (
									<div key={label}>
										<dt>{label}</dt>
										<dd>
											{label?.includes("净变化") && Number(value) > 0
												? "+"
												: ""}
											{money(value ?? null, row.currency)}
										</dd>
									</div>
								))}
							</dl>
							<p className="footnote">
								初始现金 + 全部有符号流水 = 计算余额。差额 = 账本余额 −
								计算余额。
							</p>
						</details>
						{Number(row.difference) !== 0 && (
							<p className="notice">
								差额 {money(row.difference, row.currency)}
								，请核对历史记录；此页不会修改余额。
							</p>
						)}
					</section>
				))}
			</div>
		</div>
	);
}
