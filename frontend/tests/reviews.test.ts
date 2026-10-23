import { describe, expect, it } from "bun:test";
import { filterParams, initialReviewFilter } from "../src/ReviewFilters";
import {
	coverageSchema,
	factSchema,
	journalEntrySchema,
	reviewCursorSchema,
} from "../src/reviewSchema";

describe("正式复盘接口边界", () => {
	it("拒绝缺少完整成交集合的覆盖，不能把缺失当作零成交", () => {
		// Given: 服务器漏掉覆盖集合。
		const response = {
			plan_version: 1,
			due_review_date: null,
			latest_conclusion_id: null,
		};
		// When: 表单边界解析。
		const result = coverageSchema.safeParse(response);
		// Then: 不允许生成提交依据。
		expect(result.success).toBe(false);
	});
	it("保留完整查询计数，不用空页面推断没有待办", () => {
		// Given: 返回空页但完整计数有尚未展示的主体。
		const response = {
			items: [],
			total: 36,
			round_id: "round-a",
			read_only: false,
			counts: { pending: 20, needs_update: 5, reviewed: 10, not_due: 1 },
			next_cursor: "cursor-a",
			limit: 25,
		};
		// When: 解析游标响应。
		const page = reviewCursorSchema.parse(response);
		// Then: 真实计数、轮次和游标被保留。
		expect([
			page.total,
			page.counts.pending,
			page.round_id,
			page.next_cursor,
		]).toEqual([36, 20, "round-a", "cursor-a"]);
	});
	it("拒绝无法证明轮次归属的工作台页", () => {
		// Given: 缺少轮次的页面。
		const response = {
			items: [],
			total: 0,
			read_only: false,
			counts: { pending: 0, needs_update: 0, reviewed: 0, not_due: 0 },
			next_cursor: null,
			limit: 25,
		};
		// When / Then: 不得拼接到已有列表。
		expect(reviewCursorSchema.safeParse(response).success).toBe(false);
	});
	it("未知确认时间保留null，不用市场时间替代", () => {
		// Given: 旧成交只剩市场时间。
		const response = {
			id: "order_filled:a",
			type: "order_filled",
			record_id: "a",
			occurred_at: null,
			market_time: "2026-10-23T02:00:00Z",
			order: null,
			alert: null,
			note: null,
			conclusion: null,
			plan: null,
		};
		// When: 读取事实。
		const fact = factSchema.parse(response);
		// Then: 确认时间仍未知。
		expect(fact.occurred_at).toBeNull();
		expect(fact.market_time).toBe("2026-10-23T02:00:00Z");
	});
	it("计划笔记保留服务端市场与直达主体，不要求关联订单", () => {
		// Given: 只有plan_id的美股笔记。
		const response = {
			id: "note",
			plan_id: "plan",
			order_id: null,
			text: "判断",
			created_at: "2026-10-23T02:00:00Z",
			symbol: "AAPL",
			market: "US",
			review_subject_type: "plan",
			review_subject_id: "plan",
		};
		// When: 读取笔记页。
		const note = journalEntrySchema.parse(response);
		// Then: 无需猜测订单币种仍能定位。
		expect([
			note.market,
			note.review_subject_type,
			note.review_subject_id,
		]).toEqual(["US", "plan", "plan"]);
	});
	it("筛选保留北京时间日期及证券参数，不把日期转换成客户端时区", () => {
		// Given: 同一天首尾。
		const filter = {
			...initialReviewFilter,
			symbol: " AAPL ",
			market: "US",
			from: "2026-10-23",
			to: "2026-10-23",
		};
		// When: 构造服务端完整查询。
		const params = filterParams(filter, "round-a");
		// Then: 日期原值及轮次被传递。
		expect(Object.fromEntries(params)).toEqual({
			round_id: "round-a",
			symbol: "AAPL",
			market: "US",
			start_date: "2026-10-23",
			end_date: "2026-10-23",
		});
	});
});
