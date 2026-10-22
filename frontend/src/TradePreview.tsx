import type { z } from "zod";
import type { previewSchema } from "./schema";
import { money, price, time } from "./api";
export function TradePreview({
	preview,
	currency,
	side,
}: {
	readonly preview: z.infer<typeof previewSchema>;
	readonly currency: string;
	readonly side: "buy" | "sell";
}) {
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
							1 {currency} = {preview.fx_rate} CNY · {preview.fx_source} ·{" "}
							{time(preview.fx_time)}（北京时间）
						</dd>
					</div>
				)}
				<div>
					<dt>有效至（北京时间）</dt>
					<dd>{time(preview.expires_at)}</dd>
				</div>
			</dl>
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
