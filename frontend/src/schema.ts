import { z } from "zod";
export const workerSchema = z.object({
	last_run: z.string().nullable(),
	status: z.string(),
});
// 04 后台中断：markets 含 unknown 表示日历覆盖不到、时段无法确定。
export const marketIdSchema = z.enum(["CN", "HK", "US", "unknown"]);
export type MarketId = z.infer<typeof marketIdSchema>;
export const gapSchema = z.object({
	started_at: z.string(),
	ended_at: z.string(),
	duration_minutes: z.number(),
	markets: z.array(marketIdSchema),
});
export type Gap = z.infer<typeof gapSchema>;
// 两字段可选：旧后端缺省时按无中断处理。
export const healthSchema = workerSchema.extend({
	plan_worker: workerSchema,
	recent_gaps: z.array(gapSchema).optional(),
	gap_count_7d: z.number().int().nonnegative().optional(),
});
export const decimal = z.string().regex(/^-?\d+(\.\d+)?$/);
// 0.5.0 展示字段全部可选，旧后端缺少时页面显示“不可用”。
export const changeStatusSchema = z.enum([
	"available",
	"pre_open",
	"no_previous_close",
	"no_price",
	"quote_changed",
	"market_disabled",
	"unknown",
]);
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
export const activeOrdersSchema = z.object({
	accepted_count: z.number().int().nonnegative(),
	cancel_pending_count: z.number().int().nonnegative(),
});
export const sellabilitySchema = z
	.object({
		locked_quantity: z.number().int().nonnegative(),
		next_unlock_at: z.iso.datetime({ offset: true }).nullable(),
		next_unlock_quantity: z.number().int().nonnegative(),
	})
	.refine((value) =>
		value.locked_quantity === 0
			? value.next_unlock_at === null && value.next_unlock_quantity === 0
			: value.next_unlock_at !== null &&
				value.next_unlock_quantity > 0 &&
				value.next_unlock_quantity <= value.locked_quantity,
	);
export const portfolioSchema = z.object({
	active_orders: activeOrdersSchema.nullable().optional(),
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
			sellability: sellabilitySchema.nullable().optional(),
			average_cost: decimal,
			market_price: decimal.nullable(),
			market_value: decimal.nullable(),
			pnl: decimal.nullable(),
			valuation_status: z.string(),
			previous_close: decimal.nullable().optional(),
			change: decimal.nullable().optional(),
			change_percent: decimal.nullable().optional(),
			change_day: z.string().nullable().optional(),
			change_base_day: z.string().nullable().optional(),
			change_status: changeStatusSchema.nullable().optional(),
		}),
	),
	worker: workerSchema,
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
	previous_close: decimal.nullable().optional(),
	change: decimal.nullable().optional(),
	change_percent: decimal.nullable().optional(),
	change_day: z.string().nullable().optional(),
	change_base_day: z.string().nullable().optional(),
	change_status: changeStatusSchema.nullable().optional(),
});
export type Quote = z.infer<typeof quoteSchema>;
export const historySchema = z.array(
	z.object({
		time: z.string(),
		open: decimal,
		high: decimal,
		low: decimal,
		close: decimal,
		volume: z.union([decimal, z.number()]),
		retrieved_at: z.string().nullable().optional(),
		complete: z.boolean().nullable().optional(),
	}),
);
export type HistoryRow = z.infer<typeof historySchema>[number];
export const candidateIntervalSchema = z.object({
	status: z.enum(["available", "none", "unknown"]),
	start: z.string().nullable(),
	end: z.string().nullable(),
	reason: z.string(),
});
export const previewSchema = z.object({
	position_impact: z
		.object({
			status: z.enum(["complete", "expired", "incomplete", "missing_fx"]),
			current_percent: decimal.nullable(),
			after_percent: decimal.nullable(),
			reserved_cny_percent: decimal.nullable(),
			reserved_foreign_percent: decimal.nullable(),
			remaining_quantity: z.number().int().nonnegative().nullable(),
			active_orders: z.number().int().nonnegative(),
			valuation_time: z.iso.datetime({ offset: true }).nullable(),
			quote_time: z.iso.datetime({ offset: true }),
			fx_time: z.iso.datetime({ offset: true }).nullable(),
			fx_source: z.enum(["估值汇率", "预览汇率", "人民币原币"]).nullable(),
		})
		.nullable()
		.optional(),
	stop_loss: z
		.object({
			amount: decimal,
			cny: decimal.nullable(),
			asset_percent: decimal.nullable(),
		})
		.nullable()
		.optional(),
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
export const waitingSchema = z.object({
	code: z.enum([
		"paused",
		"source_failing",
		"after_cutoff",
		"not_active",
		"awaiting_first",
		"checked_not_met",
	]),
	message: z.string(),
	checked_through: z.iso.datetime({ offset: true }).nullable(),
	since: z.iso.datetime({ offset: true }).nullable(),
});
export const orderSchema = z.object({
	first_candidate_interval: candidateIntervalSchema.optional(),
	waiting: waitingSchema.nullable().optional(),
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
// 09 隔夜摘要：最近 3 个自然日内结束的委托（只读，当前轮次）。
const endingCurrency = z.enum(["CNY", "USD", "HKD"]);
export const recentEndingSchema = z.object({
	order_id: z.string(),
	symbol: z.string(),
	name: z.string().nullable(),
	side: z.enum(["buy", "sell"]),
	quantity: z.number().int(),
	status: z.enum(["filled", "cancelled", "expired", "terminated"]),
	status_reason: z.string(),
	currency: endingCurrency,
	fill_price: decimal.nullable(),
	filled_at: z.string().nullable(),
	ended_at: z.string(),
	cash_changes: z.array(
		z.object({ currency: endingCurrency, amount: decimal }),
	),
	released_cny: decimal.nullable(),
	released_foreign: decimal.nullable(),
	released_shares: z.number().int().nullable(),
});
export type RecentEnding = z.infer<typeof recentEndingSchema>;
export const recentEndingsSchema = z.object({
	window_start: z.string(),
	window_end: z.string(),
	items: z.array(recentEndingSchema),
	more: z.boolean(),
});
// 04 建仓到清仓：当前轮次单只证券按持仓 0→正→0 分段回看，第 7 节最终契约。
// 金额与价格为 Decimal 字符串；可空字段由后端明确置 null。
export const episodeEventSchema = z.object({
	kind: z.enum([
		"first_buy",
		"add",
		"reduce",
		"close",
		"split",
		"dividend",
		"anomaly",
	]),
	at: z.string(),
	market_date: z.string(),
	currency: z.string(),
	quantity: z.number().nullable(),
	price: decimal.nullable(),
	fee: decimal.nullable(),
	position_after: z.number().nullable(),
	order_id: z.string().nullable(),
	ratio: z.string().nullable(),
	cash_per_share: decimal.nullable(),
	cash_amount: decimal.nullable(),
	payment_status: z.enum(["paid", "receivable"]).nullable(),
	note: z.string().nullable(),
});
export type EpisodeEvent = z.infer<typeof episodeEventSchema>;
export const episodeSchema = z.object({
	index: z.number().int(),
	status: z.enum(["closed", "open"]),
	opened_at: z.string(),
	closed_at: z.string().nullable(),
	opened_market_date: z.string(),
	closed_market_date: z.string().nullable(),
	holding_days: z.number().int().nonnegative(),
	buy_count: z.number().int().nonnegative(),
	sell_count: z.number().int().nonnegative(),
	peak_quantity: z.number(),
	boundary_note: z.string().nullable(),
	events: z.array(episodeEventSchema),
});
export type Episode = z.infer<typeof episodeSchema>;
export const episodesSchema = z.object({
	symbol: z.string(),
	name: z.string(),
	currency: z.string(),
	round_id: z.string(),
	as_of: z.string(),
	market_today: z.string(),
	reconciled: z.boolean(),
	reconcile_note: z.string().nullable(),
	episodes: z.array(episodeSchema),
	outside_events: z.array(episodeEventSchema),
});
export type EpisodesData = z.infer<typeof episodesSchema>;
