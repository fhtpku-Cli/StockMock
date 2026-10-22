import { z } from "zod";
import {
	decimal,
	journalSchema,
	orderSchema,
	planSnapshotSchema,
} from "./schema";

export const pageSchema = <T extends z.ZodType>(item: T) =>
	z.object({
		items: z.array(item),
		total: z.number().int().nonnegative(),
		offset: z.number().int().nonnegative(),
		limit: z.number().int().positive(),
	});
export const planSchema = planSnapshotSchema.extend({
	monitoring: z.object({
		status: z.enum(["pending", "valid", "unavailable"]),
		message: z.string(),
		checked_at: z.string().nullable(),
		market_time: z.string().nullable(),
		price: decimal.nullable(),
		source: z.string().nullable(),
	}),
});
export type Plan = z.infer<typeof planSchema>;
export type PlanSnapshot = z.infer<typeof planSnapshotSchema>;
export const planPageSchema = pageSchema(planSchema);
export const alertSchema = z.object({
	id: z.string(),
	round_id: z.string(),
	plan_id: z.string(),
	plan_version: z.number().int(),
	symbol: z.string(),
	condition: z.enum(["review_due", "target", "stop"]),
	message: z.string(),
	created_at: z.string(),
	market_time: z.string().nullable(),
	price: decimal.nullable(),
	source: z.string().nullable(),
	read_at: z.string().nullable(),
});
export const alertPageSchema = pageSchema(alertSchema).extend({
	unread_count: z.number().int().nonnegative(),
});
export const reviewSchema = z.object({
	id: z.string(),
	plan: planSchema.nullable(),
	legacy_order: orderSchema.nullable(),
	notes: journalSchema,
	order_count: z.number().int(),
	filled_count: z.number().int(),
	bought_quantity: z.number().int(),
	sold_quantity: z.number().int(),
	remaining_quantity: z.number().int(),
	account_quantity: z.number().int(),
	realized_pnl: decimal.nullable(),
	currency: z.string(),
	status: z.enum(["pending", "reviewed", "not_due"]),
	actual_status: z.string(),
});
export const reviewPageSchema = pageSchema(reviewSchema);
export type Review = z.infer<typeof reviewSchema>;
export const planStates = {
	draft: "草稿",
	active: "进行中",
	ended: "已结束",
} as const;
export const planDirections = {
	watch: "观察",
	buy: "准备买入",
	sell: "准备卖出",
} as const;
export const reviewStates = {
	pending: "待复盘",
	reviewed: "已复盘",
	not_due: "尚未进入待复盘",
} as const;
