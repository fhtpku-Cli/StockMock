import { z } from "zod";
import { decimal, ledgerSchema } from "./schema";
export const ledgerPageSchema = z.object({
	items: ledgerSchema,
	total: z.number().int(),
	offset: z.number().int(),
	limit: z.number().int(),
	boundary: z.number().int(),
	round_id: z.string(),
});
export const cashStatementSchema = z.object({
	round_id: z.string(),
	start_date: z.string().nullable(),
	end_date: z.string().nullable(),
	boundary: z.number().int(),
	currencies: z.array(
		z.object({
			currency: z.string(),
			initial_cash: decimal,
			total_inflow: decimal,
			total_outflow: decimal,
			calculated_balance: decimal,
			ledger_balance: decimal,
			difference: decimal,
			opening_balance: decimal,
			period_inflow: decimal,
			period_outflow: decimal,
			period_net_change: decimal,
			closing_balance: decimal,
			selected_net_change: decimal,
		}),
	),
});
export const ledgerKinds: Readonly<Record<string, string>> = {
	fx_out: "换汇支出",
	fx_in: "换汇收入",
	buy: "买入",
	sell: "卖出",
	fee: "费用",
	fx_buy: "自动换汇",
	fx_sell: "换回人民币",
	dividend_receivable: "应收股息登记",
	dividend: "股息直接到账",
	dividend_payment: "应收股息到账",
	split: "拆股 / 合股",
};

export function ledgerKindValue(value: string): string {
	return (
		Object.entries(ledgerKinds).find(([, label]) => label === value)?.[0] ??
		value
	);
}
