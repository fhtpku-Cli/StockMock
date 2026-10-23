import type { z } from "zod";
import { marketName, percent } from "./analysis";
import { money, time } from "./api";
import type { compositionSchema } from "./schema";
import { Panel } from "./ui";
export function Composition({
	value,
}: {
	readonly value: z.infer<typeof compositionSchema>;
}) {
	return (
		<Panel title="资产构成 · 折合人民币" className="composition">
			<div className="analysis-metrics">
				{[
					{
						name: "持仓市值",
						amount: value.positions_cny,
						weight: value.positions_percent,
						note: "证券持仓的当前参考市值",
					},
					{
						name: "现金余额",
						amount: value.cash_cny,
						weight: value.cash_percent,
						note: "包含可用和委托占用，不重复相加",
					},
					{
						name: "应收股息",
						amount: value.receivables_cny,
						weight: value.receivables_percent,
						note: "计入资产，到账前不能用于委托",
					},
				].map((item) => (
					<div className="analysis-metric" key={item.name}>
						<span>{item.name}</span>
						<strong>{money(item.amount)}</strong>
						<span>占总资产 {percent(item.weight)}</span>
						<small>{item.note}</small>
					</div>
				))}
			</div>
			<div className="analysis-groups">
				{[
					{ title: "按上市市场看持仓", rows: value.by_market, market: true },
					{ title: "按交易币种看持仓", rows: value.by_currency, market: false },
				].map((group) => (
					<div key={group.title}>
						<h3>{group.title}</h3>
						<dl className="summary-list">
							{group.rows.length ? (
								group.rows.map((row) => (
									<div key={row.key}>
										<dt>{group.market ? marketName(row.key) : row.key}</dt>
										<dd>
											{money(row.value_cny)}
											<small>占总资产 {percent(row.weight_percent)}</small>
										</dd>
									</div>
								))
							) : (
								<p className="footnote">暂无持仓。</p>
							)}
						</dl>
					</div>
				))}
			</div>
			<p className="footnote">
				两种分组是同一批持仓的不同观察方式，不能相加；不代表 ETF
				底层经济敞口。所有占比均以完整账户总资产为分母，筛选不改变分母。
			</p>
			{value.issues.map((issue) => (
				<p className="notice" key={issue}>
					{issue}
				</p>
			))}
			<details>
				<summary>查看本次估值时间</summary>
				{Object.entries(value.price_times).map(([symbol, at]) => (
					<p className="footnote" key={symbol}>
						{symbol} · {time(at)}（北京时间）
					</p>
				))}
			</details>
		</Panel>
	);
}
