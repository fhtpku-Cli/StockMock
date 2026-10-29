import { z } from "zod";
export const decimal = z.string().regex(/^-?\d+(\.\d+)?$/);
export const instrumentSchema = z.object({
	symbol: z.string(),
	name: z.string(),
	market: z.string(),
	currency: z.string(),
	kind: z.string(),
	lot_size: z.number().nullable(),
	tick_size: decimal.nullable(),
	tick_rule: z.enum(["fixed", "HK", "US"]).nullable().optional(),
	min_buy_quantity: z.number().nullable().optional(),
	quantity_step: z.number().nullable().optional(),
	rule_source: z.string().optional(),
	verified_on: z.string().nullable().optional(),
	tradable: z.boolean(),
	restriction: z.string().nullable(),
});
export type Instrument = z.infer<typeof instrumentSchema>;
export const instrumentSearchSchema = z.object({
	items: z.array(instrumentSchema),
	source: z.string(),
	message: z.string(),
});
const allocationSchema = z.object({
	key: z.string(),
	value_cny: decimal.nullable(),
	weight_percent: decimal.nullable(),
});
export const compositionSchema = z.object({
	cash_cny: decimal.nullable(),
	positions_cny: decimal.nullable(),
	receivables_cny: decimal.nullable(),
	cash_percent: decimal.nullable(),
	positions_percent: decimal.nullable(),
	receivables_percent: decimal.nullable(),
	by_market: z.array(allocationSchema),
	by_currency: z.array(allocationSchema),
	price_times: z.record(z.string(), z.string()),
	issues: z.array(z.string()),
});
export const portfolioSchema = z.object({
	composition: compositionSchema.optional(),
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
			market: z.string(),
			cost: decimal,
			pnl_percent: decimal.nullable(),
			market_value_cny: decimal.nullable(),
			weight_percent: decimal.nullable(),
			price_time: z.string().nullable(),
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
	tick_size: decimal.nullable().optional(),
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
export const candidateIntervalSchema = z.object({
	status: z.enum(["available", "none", "unknown"]),
	start: z.string().nullable(),
	end: z.string().nullable(),
	reason: z.string(),
});
export const previewSchema = z.object({
	active_at: z.string().optional(),
	first_candidate_interval: candidateIntervalSchema.optional(),
	price_bound: decimal,
	estimated_fee: decimal,
	reserved_cny: decimal,
	reserved_foreign: decimal,
	fx_rate: decimal.nullable(),
	fx_source: z.string().nullable(),
	fx_time: z.iso.datetime({ offset: true }).nullable(),
	expires_at: z.string(),
	warnings: z.array(z.string()),
});
export const planSnapshotSchema = z.object({
	id: z.string(),
	round_id: z.string(),
	symbol: z.string(),
	currency: z.string(),
	direction: z.enum(["watch", "buy", "sell"]),
	status: z.enum(["draft", "active", "ended"]),
	reason: z.string(),
	target_price: decimal.nullable(),
	stop_price: decimal.nullable(),
	review_date: z.string().nullable(),
	invalidation: z.string(),
	version: z.number().int(),
	created_at: z.string(),
	updated_at: z.string(),
});
export const fillObservationSchema = z.object({
	symbol: z.string(),
	currency: z.string(),
	source: z.string(),
	interval_start: z.iso.datetime({ offset: true }),
	market_time: z.iso.datetime({ offset: true }),
	retrieved_at: z.iso.datetime({ offset: true }),
	price: decimal,
	volume: decimal,
	tradable: z.boolean(),
});
export type FillObservation = z.infer<typeof fillObservationSchema>;
export const orderSchema = z.object({
	first_candidate_interval: candidateIntervalSchema.optional(),
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
	fill_observation: fillObservationSchema.nullable().optional(),
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
	plan_id: z.string().nullable().optional(),
	plan_version: z.number().int().nullable().optional(),
	plan_snapshot: planSnapshotSchema.nullable().optional(),
});
export type Order = z.infer<typeof orderSchema>;
export const journalSchema = z.array(
	z.object({
		id: z.string(),
		order_id: z.string().nullable(),
		plan_id: z.string().nullable().optional(),
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
