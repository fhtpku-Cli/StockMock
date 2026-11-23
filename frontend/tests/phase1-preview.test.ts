import { expect, test } from "bun:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { TradePreview } from "../src/TradePreview";
import { previewSchema } from "../src/schema";

const timestamp = "2026-11-23T15:00:00Z";
const base = {
	price_bound: "102.00",
	estimated_fee: "0.05",
	reserved_cny: "100",
	reserved_foreign: "10",
	fx_rate: "7",
	fx_time: timestamp,
	fx_source: "受控汇率",
	expires_at: timestamp,
	warnings: [],
};
const impact = {
	current_percent: null,
	after_percent: null,
	reserved_cny_percent: "4.00",
	reserved_foreign_percent: "7.01",
	remaining_quantity: null,
	active_orders: 2,
	valuation_time: timestamp,
	quote_time: timestamp,
	fx_time: timestamp,
	fx_source: "预览汇率",
};
function render(data: unknown, side: "buy" | "sell" = "buy") {
	return renderToStaticMarkup(
		createElement(TradePreview, {
			preview: previewSchema.parse(data),
			currency: "USD",
			side,
			orderType: "market",
			quantity: 100,
		}),
	);
}
test.each([
	["expired", "资产估值已过期，重新预览后可见"],
	["incomplete", "其他持仓报价暂缺，暂无法估算占总资产比例"],
	["missing_fx", "缺少汇率，暂无法估算"],
])("%s 预览保留资金占用并展示降级原因", (status, reason) => {
	const view = render({ ...base, position_impact: { ...impact, status } });
	expect(view).toContain(reason);
	expect(view).toContain("本单占用人民币约为人民币可用资金的 4.00%");
	expect(view).toContain("本单占用外币约为 USD 可用资金的 7.01%");
	expect(view).not.toContain("冻结");
	expect(view).toContain("另有 2 张活动委托未计入");
	expect(view).not.toContain("成交后约");
	const sell = render(
		{
			...base,
			position_impact: {
				...impact,
				status,
				reserved_cny_percent: null,
				reserved_foreign_percent: null,
				remaining_quantity: 0,
			},
		},
		"sell",
	);
	expect(sell).toContain("卖出后剩余 0 股 / 份");
	expect(sell).not.toContain("可用资金");
	expect(sell).not.toContain("冻结");
});
test("市价买单分开标注仓位估算和止损边界，展示估值时间与汇率依据", () => {
	const view = render({
		...base,
		stop_loss: { amount: "2.10", cny: "14.70", asset_percent: "0.03" },
		position_impact: {
			...impact,
			status: "complete",
			current_percent: "0.00",
			after_percent: "1.01",
		},
	});
	for (const label of [
		"仓位占总资产：当前 0.00% → 成交后约 1.01%",
		"按价格边界成交时占比略有不同",
		"市价单按价格边界估算",
		"跳空或延迟成交可能使实际损失更大",
		"含买卖费用约损失 US$2.10（约合 ¥14.70），相当于总资产的 0.03%。",
		"估值时间",
		"证券报价时间",
		"汇率时间",
		"预览汇率",
	])
		expect(view).toContain(label);
	expect(view).not.toContain("约占总资产");
	expect(render(base)).not.toContain("止损估算");
});
test("卖单预览展示占用持仓数量，买单仍展示资金占用", () => {
	const sell = render(
		{ ...base, reserved_cny: "0", reserved_foreign: "0" },
		"sell",
	);
	expect(sell).toContain("<dt>占用持仓</dt>");
	expect(sell).toContain("100 股 / 份（撤单确认前不可再卖）");
	expect(sell).not.toContain("占用人民币");
	expect(sell).not.toContain("占用外币");
	expect(sell).not.toContain("¥0.00");
	const buy = render(base);
	expect(buy).toContain("占用人民币");
	expect(buy).toContain("占用外币");
	expect(buy).not.toContain("占用持仓");
});
