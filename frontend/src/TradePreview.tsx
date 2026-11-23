import type { z } from "zod";
import { fullTime, money, price, time } from "./api";
import { CandidateInterval } from "./CandidateInterval";
import { fxDate, fxRate } from "./fxDisplay";
import type { previewSchema } from "./schema";
export function TradePreview({
	preview,
	currency,
	side,
	orderType,
}: {
	readonly preview: z.infer<typeof previewSchema>;
	readonly currency: string;
	readonly side: "buy" | "sell";
	readonly orderType?: "limit" | "market";
}) {
	const impact = preview.position_impact;
	const hasReference =
		currency !== "CNY" &&
		preview.fx_rate !== null &&
		Number(preview.fx_rate) > 0 &&
		preview.fx_source &&
		preview.fx_time;
	const settlement = {
		buy:
			currency === "CNY"
				? "本单使用人民币，无需换汇。"
				: Number(preview.reserved_cny) > 0
					? "已有外币不足，成交时按实际所需自动换汇；占用人民币为本单换汇预留，实际金额以成交资金流水为准。"
					: "本单使用已有外币现金，无需人民币换汇。",
		sell: "卖出款保留原币，本单不自动换回人民币。",
	} satisfies Record<typeof side, string>;
	return (
		<div className="preview-box">
			<h3>确认这笔委托</h3>
			<CandidateInterval
				interval={preview.first_candidate_interval}
				activeAt={preview.active_at}
			/>
			<dl className="summary-list">
				<div>
					<dt>价格边界</dt>
					<dd>{price(preview.price_bound, currency)}</dd>
				</div>
				<div>
					<dt>预计费用</dt>
					<dd>{money(preview.estimated_fee, currency)}</dd>
				</div>
				<div>
					<dt>占用人民币</dt>
					<dd>{money(preview.reserved_cny)}</dd>
				</div>
				<div>
					<dt>占用外币</dt>
					<dd>{money(preview.reserved_foreign, currency)}</dd>
				</div>
				{hasReference && (
					<div>
						<dt>换汇参考</dt>
						<dd>
							1 {currency} = {fxRate(preview.fx_rate)} CNY · {preview.fx_source}{" "}
							· {fxDate(preview.fx_time)}
						</dd>
					</div>
				)}
				<div>
					<dt>有效至（北京时间）</dt>
					<dd>{time(preview.expires_at)}</dd>
				</div>
			</dl>
			{preview.stop_loss && (
				<section aria-label="止损估算">
					<h4>止损估算</h4>
					<p>
						若这批买入后按止损参考价卖出，含买卖费用约损失{" "}
						{money(preview.stop_loss.amount, currency)}
						{currency !== "CNY" &&
							preview.stop_loss.cny !== null &&
							`（约合 ${money(preview.stop_loss.cny)}）`}
						{preview.stop_loss.asset_percent !== null &&
							`，约占总资产 ${preview.stop_loss.asset_percent}%`}
						。
					</p>
					<p className="footnote">
						{orderType === "market" && "市价单按价格边界估算。"}
						跳空或延迟成交可能使实际损失更大。
					</p>
				</section>
			)}
			{impact && (
				<section aria-label="仓位变化">
					<h4>仓位变化</h4>
					{impact.status === "complete" ? (
						<p>
							占总资产：当前 {impact.current_percent}% → 成交后约{" "}
							{impact.after_percent}%
						</p>
					) : (
						<>
							<p>
								{
									{
										expired: "资产估值已过期，重新预览后可见",
										incomplete: "其他持仓报价暂缺，暂无法估算占总资产比例",
										missing_fx: "缺少汇率，暂无法估算",
									}[impact.status]
								}
							</p>
							{impact.reserved_cny_percent !== null && (
								<p>
									本单冻结人民币约占人民币可用资金 {impact.reserved_cny_percent}
									%
								</p>
							)}
							{impact.reserved_foreign_percent !== null && (
								<p>
									本单冻结外币约占 {currency} 可用资金{" "}
									{impact.reserved_foreign_percent}%
								</p>
							)}
							{impact.remaining_quantity !== null && (
								<p>卖出后剩余 {impact.remaining_quantity} 股</p>
							)}
						</>
					)}
					<p className="footnote">
						仅计本单；另有 {impact.active_orders} 张活动委托未计入。
						{impact.status === "complete" &&
							orderType === "market" &&
							"按价格边界成交时占比略有不同。"}
					</p>
					<p className="footnote">
						{impact.valuation_time &&
							`估值时间 ${fullTime(impact.valuation_time)} · `}
						证券报价时间 {fullTime(impact.quote_time)}（北京时间）
						{impact.fx_source && ` · ${impact.fx_source}`}
						{impact.fx_time &&
							` · 汇率时间 ${fullTime(impact.fx_time)}（北京时间）`}
					</p>
				</section>
			)}
			<p className="footnote">{settlement[side]}</p>
			{currency !== "CNY" && !hasReference && (
				<p className="footnote">本单无人民币换汇参考。</p>
			)}
			{preview.warnings.map((warning) => (
				<p key={warning} className="notice">
					{warning}
				</p>
			))}
		</div>
	);
}
