import type { Order } from "./schema";
import { money, price, time } from "./api";
export function OrderDetails({ order: o }: { readonly order: Order }) {
	return (
		<dl className="summary-list">
			<div>
				<dt>生效时间（北京时间）</dt>
				<dd>{time(o.active_at)}</dd>
			</div>
			<div>
				<dt>有效至</dt>
				<dd>{time(o.expires_at)}</dd>
			</div>
			<div>
				<dt>成交时间 / 确认时间</dt>
				<dd>
					{o.filled_at
						? time(o.filled_at)
						: o.status === "filled"
							? "未记录"
							: "尚未成交"}{" "}
					/ {o.confirmed_at ? time(o.confirmed_at) : "未记录"}
				</dd>
			</div>
			<div>
				<dt>撤单请求时间</dt>
				<dd>{time(o.cancel_at)}</dd>
			</div>
			<div>
				<dt>成交行情来源</dt>
				<dd>
					{o.source || "历史记录未注明"} ·
					以委托生效后的完整有效样本判断成交，不按图中高低价补成交。
				</dd>
			</div>
			{o.currency !== "CNY" && o.fx_source && o.fx_time && o.fx_rate && (
				<div>
					<dt>委托锁定换汇参考</dt>
					<dd>
						1 {o.currency} = {o.fx_rate} CNY · {o.fx_source || "未注明"} ·{" "}
						{time(o.fx_time)} · 实际换汇以资金流水为准
					</dd>
				</div>
			)}
			{o.currency !== "CNY" && (!o.fx_source || !o.fx_time) && (
				<p className="footnote">
					{o.side === "sell"
						? "卖出款保留原币，本单无人民币换汇参考。"
						: "历史记录未保存换汇依据，可能使用已有外币现金；实际变动以流水为准。"}
				</p>
			)}
			{o.side === "sell" && o.status === "filled" && (
				<>
					<div>
						<dt>卖出份额成本（含买入费用）</dt>
						<dd>
							{o.cost_basis == null
								? "历史值未记录"
								: money(o.cost_basis, o.currency)}
						</dd>
					</div>
					<div>
						<dt>已实现收益（原币，扣除买卖费用）</dt>
						<dd>
							{o.realized_pnl == null
								? "历史值未记录"
								: money(o.realized_pnl, o.currency)}
						</dd>
					</div>
					<p className="footnote">
						仅为本次卖出份额的原币净收益，不含分红与换汇影响；剩余持仓浮动收益另计。
					</p>
					{o.realized_pnl == null && (
						<p className="footnote">
							此历史成交未保存成本结转，不能确认单笔收益。
						</p>
					)}
				</>
			)}
			<div>
				<dt>交易理由</dt>
				<dd>{o.reason || "未填写"}</dd>
			</div>
			<div>
				<dt>目标 / 止损参考价</dt>
				<dd>
					{o.target_price === null
						? "未记录"
						: price(o.target_price, o.currency)}{" "}
					/ {o.stop_price === null ? "未记录" : price(o.stop_price, o.currency)}
				</dd>
			</div>
		</dl>
	);
}
