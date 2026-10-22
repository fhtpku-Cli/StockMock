import { z } from "zod";
export const decimal = z.string().regex(/^-?\d+(\.\d+)?$/);
export const instrumentSchema = z.object({
	symbol: z.string(),
	name: z.string(),
	market: z.string(),
	currency: z.string(),
	kind: z.string(),
	lot_size: z.number(),
	tick_size: decimal,
	tick_rule: z.enum(["fixed", "HK", "US"]).optional(),
	min_buy_quantity: z.number().nullable().optional(),
	quantity_step: z.number().nullable().optional(),
	rule_source: z.string().optional(),
	verified_on: z.string().nullable().optional(),
	tradable: z.boolean(),
	restriction: z.string().nullable(),
});
export type Instrument = z.infer<typeof instrumentSchema>;
export const portfolioSchema = z.object({
	round_id: z.string(),
	initial_capital: decimal,
	total_assets_cny: decimal.nullable(),
	pnl_cny: decimal.nullable(),
	valuation_status: z.string(),
	cash: z.array(
		z.object({
			currency: z.string(),
			balance: decimal,
			reserved: decimal,
			available: decimal,
		}),
	),
	positions: z.array(
		z.object({
			symbol: z.string(),
			name: z.string(),
			currency: z.string(),
			quantity: z.number(),
			sellable: z.number(),
			reserved: z.number(),
			average_cost: decimal,
			market_price: decimal.nullable(),
			market_value: decimal.nullable(),
			pnl: decimal.nullable(),
			valuation_status: z.string(),
		}),
	),
	worker: z.object({ last_run: z.string().nullable(), status: z.string() }),
	warnings: z.array(z.string()),
});
export type Portfolio = z.infer<typeof portfolioSchema>;
export const quoteSchema = z.object({
	symbol: z.string(),
	price: decimal,
	currency: z.string(),
	market_time: z.string(),
	retrieved_at: z.string(),
	source: z.string(),
	tick_size: decimal.optional(),
	status: z.string(),
	message: z.string(),
});
export const historySchema = z.array(
	z.object({
		time: z.string(),
		open: decimal,
		high: decimal,
		low: decimal,
		close: decimal,
		volume: z.union([decimal, z.number()]),
	}),
);
export const previewSchema = z.object({
	price_bound: decimal,
	estimated_fee: decimal,
	reserved_cny: decimal,
	reserved_foreign: decimal,
	fx_rate: decimal.nullable(),
	expires_at: z.string(),
	warnings: z.array(z.string()),
});
export const orderSchema = z.object({
	id: z.string(),
	request_id: z.string(),
	symbol: z.string(),
	side: z.enum(["buy", "sell"]),
	order_type: z.enum(["limit", "market"]),
	quantity: z.number(),
	limit_price: decimal.nullable(),
	price_bound: decimal,
	status: z.enum([
		"accepted",
		"cancel_pending",
		"filled",
		"cancelled",
		"expired",
		"terminated",
	]),
	status_reason: z.string(),
	created_at: z.string(),
	active_at: z.string(),
	expires_at: z.string(),
	cancel_at: z.string().nullable(),
	filled_at: z.string().nullable(),
	confirmed_at: z.string().nullable(),
	fill_price: decimal.nullable(),
	fee: decimal.nullable(),
	currency: z.string(),
	reserved_cny: decimal,
	reserved_foreign: decimal,
	fx_rate: decimal.nullable(),
	source: z.string().optional(),
	cost_basis: decimal.nullable().optional(),
	realized_pnl: decimal.nullable().optional(),
	fx_source: z.string().nullable(),
	fx_time: z.string().nullable(),
	reason: z.string(),
	target_price: decimal.nullable(),
	stop_price: decimal.nullable(),
});
export type Order = z.infer<typeof orderSchema>;
export const journalSchema = z.array(
	z.object({
		id: z.string(),
		order_id: z.string().nullable(),
		text: z.string(),
		created_at: z.string(),
	}),
);
export const ledgerSchema = z.array(
	z.object({
		id: z.string(),
		kind: z.string(),
		currency: z.string(),
		amount: decimal,
		created_at: z.string(),
		order_id: z.string().nullable(),
		note: z.string(),
	}),
);
export const fxSchema = z.array(
	z.object({
		currency: z.string(),
		cny_per_unit: decimal,
		market_time: z.string(),
		source: z.string(),
	}),
);
export const roundsSchema = z.array(
	z.object({
		id: z.string(),
		created_at: z.string(),
		archived_at: z.string().nullable(),
	}),
);
export const sessionSchema = z.object({
	authenticated: z.boolean(),
	configured: z.boolean(),
});
export const acknowledgmentSchema = z.unknown();
