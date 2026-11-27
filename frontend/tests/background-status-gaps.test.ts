import { afterEach, beforeEach, expect, spyOn, test } from "bun:test";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import {
	BackgroundStatus,
	backgroundStatusQuery,
	GapDetails,
	gapLineText,
	gapMarketsText,
	gapRangeText,
	gapSummaryText,
	GAP_RECOVERY_NOTE,
} from "../src/BackgroundStatus";
import { type Gap, gapSchema, healthSchema } from "../src/schema";

// 受控数据只覆盖 /api/health 中断增量契约，不连接任何账户服务。
const gap = (overrides: Partial<Gap>): Gap =>
	gapSchema.parse({
		started_at: "2026-11-27T19:09:00Z",
		ended_at: "2026-11-27T20:10:00Z",
		duration_minutes: 61,
		markets: ["US"],
		...overrides,
	});

test("中断涉及市场用文字表达，unknown 表示时段无法确定", () => {
	expect(gapMarketsText(["US"])).toBe("涉及美股");
	expect(gapMarketsText(["CN", "US"])).toBe("涉及A股、美股");
	expect(gapMarketsText(["unknown"])).toBe("时段无法确定");
	expect(gapMarketsText(["US", "unknown"])).toBe(
		"涉及美股，部分时段无法确定",
	);
	expect(gapMarketsText([])).toBe("");
});

test("中断区间同一北京日期只写一次，跨日两端都带日期", () => {
	expect(gapRangeText(gap())).toBe("11/28 03:09–04:10");
	expect(
		gapRangeText(
			gap({
				started_at: "2026-11-27T15:50:00Z",
				ended_at: "2026-11-27T16:40:00Z",
			}),
		),
	).toBe("11/27 23:50–11/28 00:40");
});

test("单条中断与汇总文案", () => {
	expect(gapLineText(gap())).toBe("11/28 03:09–04:10（61 分钟，涉及美股）");
	expect(
		gapLineText(gap({ duration_minutes: 20, markets: ["CN", "HK"] })),
	).toBe("11/28 03:09–04:10（20 分钟，涉及A股、港股）");
	expect(gapSummaryText(2, gap())).toBe(
		"近 7 天交易时段内后台中断 2 次，最近一次 11/28 03:09–04:10（61 分钟，涉及美股）。",
	);
	// 次数为近 7 天全部检测到的间隔，与展示条数无关。
	expect(gapSummaryText(7, null)).toBe("近 7 天交易时段内后台中断 7 次。");
});

test("healthSchema 两字段可选，旧后端缺省按空处理", () => {
	const legacy = {
		last_run: "2026-11-28T04:11:00Z",
		status: "running",
		plan_worker: { last_run: "2026-11-28T04:11:00Z", status: "running" },
	};
	const parsed = healthSchema.parse(legacy);
	expect(parsed.recent_gaps).toBeUndefined();
	expect(parsed.gap_count_7d).toBeUndefined();
	const modern = healthSchema.parse({
		...legacy,
		recent_gaps: [gap()],
		gap_count_7d: 1,
	});
	expect(modern.recent_gaps).toHaveLength(1);
	expect(modern.gap_count_7d).toBe(1);
	expect(
		healthSchema.safeParse({ ...legacy, recent_gaps: [{ ...gap(), markets: ["XX"] }] })
			.success,
	).toBe(false);
	expect(
		healthSchema.safeParse({ ...legacy, gap_count_7d: -1 }).success,
	).toBe(false);
});

const OriginalRequest = globalThis.Request;
const originalFetch = globalThis.fetch;
let health: Record<string, unknown>;
let client: QueryClient;
let clock: ReturnType<typeof spyOn>;

beforeEach(() => {
	clock = spyOn(Date, "now").mockImplementation(() =>
		Date.parse("2026-11-28T05:00:00Z"),
	);
	client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
	health = {
		last_run: "2026-11-28T04:59:00Z",
		status: "running",
		plan_worker: { last_run: "2026-11-28T04:59:00Z", status: "running" },
	};
	globalThis.Request = class extends OriginalRequest {
		constructor(input: RequestInfo | URL, init?: RequestInit) {
			super(
				typeof input === "string"
					? new URL(input, "http://gaps-test.invalid")
					: input,
				init,
			);
		}
	};
	globalThis.fetch = Object.assign(
		async (input: RequestInfo | URL) => {
			const request = input as Request;
			expect(request.url).toBe("http://gaps-test.invalid/api/health");
			expect(request.method).toBe("GET");
			return Response.json(health);
		},
		{ preconnect: originalFetch.preconnect },
	);
});
afterEach(() => {
	client.clear();
	clock.mockRestore();
	globalThis.Request = OriginalRequest;
	globalThis.fetch = originalFetch;
});

const markup = () =>
	renderToStaticMarkup(
		createElement(
			QueryClientProvider,
			{ client },
			createElement(BackgroundStatus),
		),
	);
const load = async () => {
	await client.fetchQuery(backgroundStatusQuery(client));
};

test("无中断时不显示额外内容，兼容旧后端缺省", async () => {
	await load();
	expect(markup()).not.toContain("后台中断");
	// 有字段但为零次同样不显示。
	health = { ...health, recent_gaps: [], gap_count_7d: 0 };
	await load();
	expect(markup()).not.toContain("后台中断");
});

test("有中断时显示一句汇总与恢复说明，明细默认折叠", async () => {
	health = {
		...health,
		recent_gaps: [
			gap(),
			gap({
				started_at: "2026-11-26T01:00:00Z",
				ended_at: "2026-11-26T01:20:00Z",
				duration_minutes: 20,
				markets: ["CN", "HK"],
			}),
		],
		gap_count_7d: 2,
	};
	await load();
	const html = markup();
	expect(html).toContain("交易处理 · 正常");
	expect(html).toContain(
		"近 7 天交易时段内后台中断 2 次，最近一次 11/28 03:09–04:10（61 分钟，涉及美股）。",
	);
	expect(html).toContain(GAP_RECOVERY_NOTE);
	expect(html).toContain("展开中断明细");
	// 明细默认折叠，其余条目不渲染。
	expect(html).not.toContain("worker-gap-list");
	expect(html).not.toContain("11/26 09:00–09:20");
});

test("展开后最多显示 5 条，超过时说明仅列最近 5 条", () => {
	// 后端契约：recent_gaps 最多 5 条，gap_count_7d 为全部次数。
	const gaps = Array.from({ length: 7 }, (_, i) =>
		gap({ duration_minutes: 10 + i }),
	);
	const html = renderToStaticMarkup(
		createElement(GapDetails, { count: 7, gaps: gaps.slice(0, 5) }),
	);
	expect(html.match(/<li>/g)).toHaveLength(5);
	expect(html).toContain("共 7 次，仅列出最近 5 次。");
	expect(html).toContain("11/28 03:09–04:10（10 分钟，涉及美股）");
	const compact = renderToStaticMarkup(
		createElement(GapDetails, { count: 3, gaps: gaps.slice(0, 3) }),
	);
	expect(compact.match(/<li>/g)).toHaveLength(3);
	expect(compact).not.toContain("仅列出");
});
