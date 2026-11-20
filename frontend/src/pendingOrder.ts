import { useSyncExternalStore } from "react";
import { z } from "zod";
import { ApiError, api } from "./api";
import { orderSchema } from "./schema";

export const PENDING_ORDER_KEY = "stockmock.pending-order";
const changed = "stockmock:pending-order-changed";
export const submissionSchema = z
	.object({
		request_id: z.string().min(1),
		expected_round_id: z.string().min(1),
		expected_expires_at: z.string().min(1),
		symbol: z.string().min(1),
		side: z.enum(["buy", "sell"]),
		order_type: z.enum(["limit", "market"]),
		quantity: z.number().int().positive(),
		limit_price: z.string().optional(),
		reason: z.string(),
		target_price: z.string().optional(),
		stop_price: z.string().optional(),
		plan_id: z.string().optional(),
		plan_version: z.number().int().optional(),
	})
	.strict();
export type Submission = z.infer<typeof submissionSchema>;

export function readPendingOrder(): Submission | null {
	try {
		const saved = sessionStorage.getItem(PENDING_ORDER_KEY);
		return saved === null ? null : submissionSchema.parse(JSON.parse(saved));
	} catch (error) {
		if (!(error instanceof Error)) throw error;
		throw new ApiError(
			"recovery_storage",
			"无法读取待确认委托。请恢复此标签页的会话存储后重试。",
		);
	}
}

export function savePendingOrder(submission: Submission): void {
	const pending = readPendingOrder();
	if (
		pending &&
		JSON.stringify(pending) !==
			JSON.stringify(submissionSchema.parse(submission))
	)
		throw new ApiError(
			"pending_order",
			"请先核对上一笔委托结果，再建立新委托。",
		);
	try {
		const saved = JSON.stringify(submissionSchema.parse(submission));
		sessionStorage.setItem(PENDING_ORDER_KEY, saved);
		if (sessionStorage.getItem(PENDING_ORDER_KEY) !== saved)
			throw new ApiError(
				"recovery_storage",
				"委托恢复记录未保存，暂不能提交。",
			);
	} catch (error) {
		if (!(error instanceof Error)) throw error;
		throw new ApiError(
			"recovery_storage",
			"无法保存委托恢复记录，暂不能提交。请允许此标签页使用会话存储后重试。",
		);
	}
	window.dispatchEvent(new Event(changed));
}

export function clearPendingOrder(requestId: string): void {
	if (readPendingOrder()?.request_id !== requestId) return;
	sessionStorage.removeItem(PENDING_ORDER_KEY);
	if (sessionStorage.getItem(PENDING_ORDER_KEY) !== null)
		throw new ApiError(
			"recovery_storage",
			"委托核对记录暂未清除，请恢复会话存储后重试。",
		);
	window.dispatchEvent(new Event(changed));
}

function snapshot(): string | null {
	try {
		return sessionStorage.getItem(PENDING_ORDER_KEY);
	} catch (error) {
		if (!(error instanceof Error)) throw error;
		return null;
	}
}
function subscribe(notify: () => void): () => void {
	window.addEventListener(changed, notify);
	return () => window.removeEventListener(changed, notify);
}
export function usePendingOrder(): boolean {
	return useSyncExternalStore(subscribe, snapshot) !== null;
}

export async function findOrderReceipt(requestId: string) {
	try {
		return await api(
			`orders/receipts/${encodeURIComponent(requestId)}`,
			orderSchema,
		);
	} catch (error) {
		if (error instanceof ApiError && error.status === 404) return null;
		throw error;
	}
}

export function isFinalOrderRejection(
	error: unknown,
	recovery = false,
): boolean {
	if (recovery)
		return (
			error instanceof ApiError &&
			error.code === "order_receipt_rejected" &&
			error.status === 409
		);
	return (
		error instanceof ApiError &&
		error.status !== undefined &&
		[400, 409, 422].includes(error.status) &&
		!["idempotency_conflict", "request_failed"].includes(error.code)
	);
}
