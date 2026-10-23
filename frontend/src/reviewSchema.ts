import { z } from "zod";
import { alertSchema, planSchema, reviewSchema } from "./planSchema";
import { journalSchema, orderSchema } from "./schema";

export const coverageSchema = z.object({
	plan_version: z.number().int().nullable(),
	filled_order_ids: z.array(z.string()),
	due_review_date: z.string().nullable(),
	latest_conclusion_id: z.string().nullable(),
});
export const conclusionSchema = z.object({
	id: z.string(),
	round_id: z.string(),
	subject_type: z.enum(["plan", "order"]),
	subject_id: z.string(),
	conclusion: z.string(),
	deviation: z.string(),
	next_action: z.string(),
	created_at: z.string(),
	coverage: coverageSchema,
});
export const review04Schema = reviewSchema.extend({
	review_state: z.enum(["pending", "needs_update", "reviewed", "not_due"]),
	update_reasons: z.array(z.string()),
	subject_type: z.enum(["plan", "order"]),
	symbol: z.string().nullable(),
	market: z.string().nullable(),
	created_at: z.string(),
	latest_record_at: z.string(),
	coverage: coverageSchema,
});
export const reviewDetailSchema = z.object({
	round_id: z.string(),
	read_only: z.boolean(),
	review: review04Schema,
	coverage: coverageSchema,
	orders: z.array(orderSchema),
	conclusions: z.array(conclusionSchema),
});
export const conclusionResultSchema = z.object({
	conclusion: conclusionSchema,
	round_id: z.string(),
	read_only: z.boolean(),
	review: review04Schema,
});
export const reviewCursorSchema = z.object({
	items: z.array(review04Schema),
	total: z.number().int().nonnegative(),
	round_id: z.string(),
	read_only: z.boolean(),
	counts: z.object({
		pending: z.number().int(),
		needs_update: z.number().int(),
		reviewed: z.number().int(),
		not_due: z.number().int(),
	}),
	next_cursor: z.string().nullable(),
	limit: z.number().int().positive(),
});
export const factSchema = z.object({
	id: z.string(),
	type: z.enum([
		"order_submitted",
		"order_filled",
		"cancel_requested",
		"order_terminal",
		"plan_created",
		"plan_updated",
		"alert",
		"note",
		"conclusion",
	]),
	record_id: z.string(),
	occurred_at: z.string().nullable(),
	market_time: z.string().nullable(),
	order: orderSchema.nullable(),
	alert: alertSchema.nullable(),
	note: journalSchema.element.nullable(),
	conclusion: conclusionSchema.nullable(),
	plan: planSchema.nullable(),
});
export const journalEntrySchema = journalSchema.element.extend({
	symbol: z.string().nullable(),
	market: z.string().nullable(),
	review_subject_type: z.enum(["plan", "order"]).nullable(),
	review_subject_id: z.string().nullable(),
});
export type ReviewDetailData = z.infer<typeof reviewDetailSchema>;
export type Coverage = z.infer<typeof coverageSchema>;
export type ReviewTarget = {
	readonly roundId: string;
	readonly subjectType: "plan" | "order";
	readonly subjectId: string;
};
export const reviewStateNames = {
	pending: "待首次复盘",
	needs_update: "有新进展待补充",
	reviewed: "已复盘",
	not_due: "暂未到复盘时点",
} as const;
export const targetPath = (target: ReviewTarget) =>
	`reviews/${target.subjectType}/${encodeURIComponent(target.subjectId)}`;
