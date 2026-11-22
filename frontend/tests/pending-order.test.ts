import { afterEach, beforeEach, describe, expect, test } from "bun:test";
import { ApiError } from "../src/api";
import {
	clearPendingOrder,
	isFinalOrderRejection,
	PENDING_ORDER_KEY,
	readPendingOrder,
	readPendingOrderRecord,
	type Submission,
	savePendingOrder,
	submissionSchema,
} from "../src/pendingOrder";

const submission: Submission = {
	request_id: "original-request",
	expected_round_id: "original-round",
	expected_expires_at: "2026-11-20T20:00:00Z",
	symbol: "AAPL",
	side: "buy",
	order_type: "limit",
	quantity: 2,
	limit_price: "100.00",
	reason: "保留完整原委托",
	target_price: "110.00",
	stop_price: "90.00",
	plan_id: "original-plan",
	plan_version: 3,
};
const originalStorage = Object.getOwnPropertyDescriptor(
	globalThis,
	"sessionStorage",
);
const originalWindow = Object.getOwnPropertyDescriptor(globalThis, "window");
let values: Map<string, string>;
let events: EventTarget;
let storage: Storage;
beforeEach(() => {
	values = new Map();
	events = new EventTarget();
	storage = {
		get length() {
			return values.size;
		},
		clear: () => values.clear(),
		getItem: (key) => values.get(key) ?? null,
		key: (index) => Array.from(values.keys())[index] ?? null,
		removeItem: (key) => {
			values.delete(key);
		},
		setItem: (key, value) => {
			values.set(key, value);
		},
	};
	Object.defineProperty(globalThis, "sessionStorage", {
		configurable: true,
		value: storage,
	});
	Object.defineProperty(globalThis, "window", {
		configurable: true,
		value: events,
	});
});
afterEach(() => {
	if (originalStorage)
		Object.defineProperty(globalThis, "sessionStorage", originalStorage);
	else Reflect.deleteProperty(globalThis, "sessionStorage");
	if (originalWindow)
		Object.defineProperty(globalThis, "window", originalWindow);
	else Reflect.deleteProperty(globalThis, "window");
});

describe("同标签页委托保存与确认", () => {
	test("发送边界保存完整内容，重新读取及同号重试保持原轮次和所有可选字段", () => {
		savePendingOrder(submission);
		expect(JSON.parse(values.get(PENDING_ORDER_KEY) ?? "null")).toEqual(
			submission,
		);
		expect(readPendingOrder()).toEqual(submission);
		savePendingOrder(submission);
		expect(readPendingOrder()).toEqual(submission);
	});
	test("展示币种随原委托保留，重读和重试只取严格请求字段", () => {
		savePendingOrder(submission, "USD");
		expect(readPendingOrderRecord()).toEqual({ submission, currency: "USD" });
		expect(submissionSchema.parse(readPendingOrder())).toEqual(submission);
		expect(readPendingOrder()).not.toHaveProperty("display_currency");
		savePendingOrder(submission);
		expect(readPendingOrderRecord()?.currency).toBe("USD");
		expect(() =>
			savePendingOrder({ ...submission, quantity: 3 }, "USD"),
		).toThrow("请先核对");
	});
	test.each([undefined, "", "invalid currency", "EUR"])(
		"旧记录或无效展示币种 %s 仍能读取原请求",
		(currency) => {
			values.set(
				PENDING_ORDER_KEY,
				JSON.stringify({ ...submission, display_currency: currency }),
			);
			expect(readPendingOrderRecord()).toEqual({
				submission,
				currency: undefined,
			});
			expect(readPendingOrder()).toEqual(submission);
		},
	);
	test("未决记录阻止新号和同号改内容，旧记录完整保留", () => {
		savePendingOrder(submission);
		expect(() =>
			savePendingOrder({ ...submission, request_id: "new-request" }),
		).toThrow("请先核对");
		expect(() => savePendingOrder({ ...submission, quantity: 3 })).toThrow(
			"请先核对",
		);
		expect(readPendingOrder()).toEqual(submission);
	});
	test.each(["throws", "silent"])("存储写入 %s 失败时发送边界抛错", (mode) => {
		storage.setItem = () => {
			if (mode === "throws")
				throw new DOMException("quota", "QuotaExceededError");
		};
		let postCount = 0;
		expect(() => {
			savePendingOrder(submission);
			postCount += 1;
		}).toThrow("无法保存");
		expect(postCount).toBe(0);
	});
	test("确认前记录持续存在，确认后允许有意的第二笔同参数委托", () => {
		savePendingOrder(submission);
		expect(readPendingOrder()).toEqual(submission);
		clearPendingOrder(submission.request_id);
		expect(readPendingOrder()).toBeNull();
		const second = { ...submission, request_id: "second-request" };
		savePendingOrder(second);
		expect(readPendingOrder()).toEqual(second);
	});
	test("延迟旧回执清理不移除新委托", () => {
		savePendingOrder({ ...submission, request_id: "second-request" });
		clearPendingOrder(submission.request_id);
		expect(readPendingOrder()?.request_id).toBe("second-request");
	});
	test("清除失败必须报告错误，不能声称已经完成确认", () => {
		savePendingOrder(submission);
		storage.removeItem = () => {};
		expect(() => clearPendingOrder(submission.request_id)).toThrow("暂未清除");
		expect(readPendingOrder()).toEqual(submission);
	});
	test("损坏记录禁止被新单覆盖", () => {
		values.set(PENDING_ORDER_KEY, "{");
		expect(() => savePendingOrder(submission)).toThrow("无法读取");
		expect(values.get(PENDING_ORDER_KEY)).toBe("{");
	});
});

describe("原请求是否已明确拒绝", () => {
	test.each([
		new ApiError("unauthorized", "登录过期", 401),
		new ApiError("not_found", "暂未找到", 404),
		new ApiError("connection", "网络中断"),
		new ApiError("request_failed", "服务端失败", 500),
		new ApiError("request_failed", "未知拒绝", 400),
		new ApiError("idempotency_conflict", "原内容冲突", 409),
	])("不确定结果保留原记录 %o", (error) => {
		expect(isFinalOrderRejection(error)).toBe(false);
	});
	test.each(["round_changed", "validity_changed", "insufficient_cash"])(
		"明确业务拒绝 %s 可结束原请求",
		(code) => {
			expect(isFinalOrderRejection(new ApiError(code, "明确拒绝", 409))).toBe(
				true,
			);
		},
	);
});

describe("恢复重试的最终拒绝", () => {
	test.each([
		"round_changed",
		"validity_changed",
		"insufficient_cash",
		"idempotency_conflict",
	])("普通业务错误 %s 不能结束仍可能在途的原请求", (code) => {
		expect(
			isFinalOrderRejection(new ApiError(code, "重试拒绝", 409), true),
		).toBe(false);
	});
	test("只有后端已持久化的拒绝可以结束恢复", () => {
		expect(
			isFinalOrderRejection(
				new ApiError("order_receipt_rejected", "原单最终拒绝", 409),
				true,
			),
		).toBe(true);
		expect(
			isFinalOrderRejection(new ApiError("unauthorized", "请登录", 401), true),
		).toBe(false);
		expect(
			isFinalOrderRejection(new ApiError("connection", "网络异常"), true),
		).toBe(false);
	});
});
