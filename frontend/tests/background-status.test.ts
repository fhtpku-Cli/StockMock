import { afterEach, beforeEach, describe, expect, spyOn, test } from "bun:test";
import {
	QueryClient,
	QueryClientProvider,
	QueryObserver,
} from "@tanstack/react-query";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import {
	BackgroundStatus,
	backgroundStatusQuery,
} from "../src/BackgroundStatus";

const OriginalRequest = globalThis.Request;
const originalFetch = globalThis.fetch;
const beginning = Date.parse("2026-11-23T01:00:00Z");
let now = beginning;
let health: {
	last_run: string | null;
	status: string;
	plan_worker: { last_run: string | null; status: string };
};
let client: QueryClient;
let clock: ReturnType<typeof spyOn>;
let reads: number;

beforeEach(() => {
	now = beginning;
	reads = 0;
	clock = spyOn(Date, "now").mockImplementation(() => now);
	client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
	health = {
		last_run: null,
		status: "starting",
		plan_worker: { last_run: null, status: "starting" },
	};
	globalThis.Request = class extends OriginalRequest {
		constructor(input: RequestInfo | URL, init?: RequestInit) {
			super(
				typeof input === "string"
					? new URL(input, "http://health-test.invalid")
					: input,
				init,
			);
		}
	};
	globalThis.fetch = Object.assign(
		async (input: RequestInfo | URL) => {
			const request = input as Request;
			expect(request.url).toBe("http://health-test.invalid/api/health");
			expect(request.method).toBe("GET");
			reads += 1;
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
const refresh = () => client.fetchQuery(backgroundStatusQuery(client));

describe("后台首轮等待计时", () => {
	test("切页卸载再返回持续累计三分钟，离开超过默认五分钟仍保留", async () => {
		const observer = new QueryObserver(client, backgroundStatusQuery(client));
		const unsubscribe = observer.subscribe(() => {});
		await refresh();
		expect(markup()).not.toContain("可能延迟");
		now += 120000;
		unsubscribe();
		expect(client.getQueryCache().find({ queryKey: ["health"] })?.gcTime).toBe(
			Infinity,
		);
		await refresh();
		expect(markup()).not.toContain("可能延迟");
		now = beginning + 179999;
		await refresh();
		expect(markup()).not.toContain("可能延迟");
		now += 1;
		await refresh();
		expect(markup().match(/可能延迟/g)).toHaveLength(2);
		now += 360000;
		const returned = new QueryObserver(client, backgroundStatusQuery(client));
		const stop = returned.subscribe(() => {});
		await refresh();
		expect(markup().match(/可能延迟/g)).toHaveLength(2);
		expect((await refresh()).tradeObservedAt).toBe(beginning);
		stop();
		expect(reads).toBeGreaterThanOrEqual(5);
	});
	test("收到新的 last_run 后各后台分别清除延迟提示", async () => {
		await refresh();
		now += 180000;
		expect(markup().match(/可能延迟/g)).toHaveLength(2);
		health.last_run = new Date(now).toISOString();
		health.status = "running";
		const trade = await refresh();
		expect(trade.tradeObservedAt).toBeNull();
		expect(trade.planObservedAt).toBe(beginning);
		expect(markup()).toContain("交易处理 · 正常");
		expect(markup()).toContain("计划观察 · 可能延迟");
		health.plan_worker = {
			last_run: new Date(now).toISOString(),
			status: "running",
		};
		await refresh();
		expect(markup()).not.toContain("可能延迟");
	});
	test("退出或过期清理会话查询后重新开始首轮计时", async () => {
		await refresh();
		now += 600000;
		expect(markup().match(/可能延迟/g)).toHaveLength(2);
		await client.cancelQueries();
		client.removeQueries({
			predicate: (query) => query.queryKey[0] !== "session",
		});
		const resumed = await refresh();
		expect(resumed.tradeObservedAt).toBe(now);
		expect(resumed.planObservedAt).toBe(now);
		expect(markup()).not.toContain("可能延迟");
	});
	test("两个后台首次无记录的时刻分别保存", async () => {
		health.plan_worker.last_run = new Date(now).toISOString();
		await refresh();
		now += 120000;
		health.plan_worker.last_run = null;
		const second = await refresh();
		expect(second.tradeObservedAt).toBe(beginning);
		expect(second.planObservedAt).toBe(beginning + 120000);
		now += 60000;
		expect(markup()).toContain("交易处理 · 可能延迟");
		expect(markup()).toContain("计划观察 · 准备中");
	});
});
