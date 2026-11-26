import { beforeAll, expect, test } from "bun:test";
import { existsSync } from "node:fs";
import { expect as check, chromium, type Page } from "@playwright/test";
import {
	DRAWDOWN_BROKEN_NOTE,
	snapshotRangeSchema,
	snapshotSchema,
} from "../src/history";

let js = "",
	css = "";
beforeAll(async () => {
	const build = await Bun.build({
		entrypoints: [
			new URL("./asset-drawdown-fixture.tsx", import.meta.url).pathname,
		],
		target: "browser",
		format: "iife",
		define: { "process.env.NODE_ENV": '"production"' },
	});
	if (!build.success) throw new Error("资产回落夹具构建失败");
	for (const asset of build.outputs) {
		if (asset.path.endsWith(".js")) js = await asset.text();
		if (asset.path.endsWith(".css")) css = await asset.text();
	}
});

type Day = {
	day: string;
	assets: string;
	status?: "complete" | "missing" | "stale";
};
const snapshot = ({ day, assets, status = "complete" }: Day) =>
	snapshotSchema.parse({
		id: `s-${day}`,
		round_id: "r1",
		day,
		captured_at: `${day}T09:31:00+08:00`,
		total_assets_cny: status === "missing" ? null : assets,
		pnl_cny: status === "missing" ? null : "0",
		status,
		issues: [],
		price_times: {},
	});
const range = (days: readonly Day[], drawdown?: unknown) => {
	const items = days.map(snapshot);
	const complete = items.filter((i) => i.status === "complete");
	return snapshotRangeSchema.parse({
		round_id: "r1",
		start_date: null,
		end_date: null,
		items,
		recorded_days: items.length,
		complete_days: complete.length,
		missing_days: items.length - complete.length,
		first: items[0] ?? null,
		last: items.at(-1) ?? null,
		change_cny: "0",
		continuous: true,
		reason: "受控区间摘要说明",
		...(drawdown === undefined ? {} : { drawdown }),
	});
};

// 场景一：跨年区间，高点 2025-12-30、低点 2026-01-02，中间有断档。
const crossYear = range(
	[
		{ day: "2025-12-28", assets: "9800", status: "stale" },
		{ day: "2025-12-29", assets: "9900" },
		{ day: "2025-12-30", assets: "10000" },
		{ day: "2025-12-31", assets: "9200" },
		{ day: "2026-01-01", assets: "8000" },
		{ day: "2026-01-02", assets: "7500" },
		{ day: "2026-01-03", assets: "0", status: "missing" },
		{ day: "2026-01-04", assets: "8000" },
	],
	{
		status: "computed",
		reason: "受控回落说明",
		peak: {
			day: "2025-12-30",
			captured_at: "2025-12-30T09:31:00+08:00",
			total_assets_cny: "10000",
		},
		trough: {
			day: "2026-01-02",
			captured_at: "2026-01-02T10:02:00+08:00",
			total_assets_cny: "7500",
		},
		amount_cny: "2500",
		percent: "25.00",
		segments_used: 1,
		broken_days: 1,
	},
);
// 场景二：同年区间，完整记录，回落不足两天之外无断档。
const sameYear = range(
	[
		{ day: "2026-11-18", assets: "9800" },
		{ day: "2026-11-19", assets: "9900" },
		{ day: "2026-11-20", assets: "10000" },
		{ day: "2026-11-21", assets: "9200" },
		{ day: "2026-11-22", assets: "8750" },
		{ day: "2026-11-23", assets: "8900" },
	],
	{
		status: "computed",
		reason: "受控回落说明",
		peak: {
			day: "2026-11-20",
			captured_at: "2026-11-20T09:31:00+08:00",
			total_assets_cny: "10000",
		},
		trough: {
			day: "2026-11-22",
			captured_at: "2026-11-22T10:02:00+08:00",
			total_assets_cny: "8750",
		},
		amount_cny: "1250",
		percent: "12.50",
		segments_used: 1,
		broken_days: 0,
	},
);
// 场景三：有可计算的连续段，但没有出现回落。
const noDrop = range(
	[
		{ day: "2026-11-18", assets: "9000" },
		{ day: "2026-11-19", assets: "9500" },
		{ day: "2026-11-20", assets: "10000" },
	],
	{
		status: "no_drop",
		reason: "受控无回落说明",
		peak: null,
		trough: null,
		amount_cny: null,
		percent: null,
		segments_used: 1,
		broken_days: 0,
	},
);
// 场景四：连续完整记录不足两天。
const insufficient = range([{ day: "2026-11-18", assets: "9000" }], {
	status: "insufficient",
	reason: "受控不足说明",
	peak: null,
	trough: null,
	amount_cny: null,
	percent: null,
	segments_used: 0,
	broken_days: 0,
});
// 场景五：旧后端不返回 drawdown 字段。
const legacy = range([
	{ day: "2026-11-18", assets: "9000" },
	{ day: "2026-11-19", assets: "9500" },
]);

const scenarios = {
	跨年区间: crossYear,
	同年区间: sameYear,
	没有回落: noDrop,
	记录不足: insufficient,
	旧后端无字段: legacy,
} as const;

test.each(Object.keys(scenarios))(
	"资产回落：%s",
	async (name) => {
		const executablePath =
			process.env.CHROME_PATH ??
			[
				"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
				"/usr/bin/google-chrome",
			].find(existsSync);
		const browser = await chromium.launch(
			executablePath ? { executablePath } : {},
		);
		const page = await browser.newPage();
		try {
			await page.route("**/*", async (route) => {
				const u = new URL(route.request().url());
				if (u.pathname === "/")
					return route.fulfill({
						contentType: "text/html",
						body: '<meta name="viewport" content="width=device-width"><div id="root"></div>',
					});
				if (u.pathname === "/api/snapshots/range")
					return route.fulfill({
						json: scenarios[name as keyof typeof scenarios],
					});
				return route.abort();
			});
			await page.goto("http://127.0.0.1:18922/");
			await page.addScriptTag({ content: js });
			await page.addStyleTag({ content: css });
			await check(page.getByText("当前显示：")).toBeVisible();
			const line = page.locator(".history-drawdown");
			if (name === "跨年区间") {
				await check(line).toContainText(
					"区间内最大回落 −25.00%（¥2,500.00）：从 2025-12-30 09:31 的 ¥10,000.00 回落到 2026-01-02 10:02 的 ¥7,500.00",
				);
				await check(line).toContainText(DRAWDOWN_BROKEN_NOTE);
			} else if (name === "同年区间") {
				await check(line).toContainText(
					"区间内最大回落 −12.50%（¥1,250.00）：从 11-20 09:31 的 ¥10,000.00 回落到 11-22 10:02 的 ¥8,750.00",
				);
				await check(line).not.toContainText(DRAWDOWN_BROKEN_NOTE);
			} else if (name === "没有回落") {
				await check(line).toContainText("区间内连续完整记录中没有出现回落");
			} else if (name === "记录不足") {
				await check(line).toContainText("连续完整记录不足两天，无法计算回落");
			} else {
				await check(line).toHaveCount(0);
			}
			await assertChart(page, name);
		} finally {
			await browser.close();
		}
	},
	20000,
);

/** 图上高点、低点标记与普通圆点区分，并带无障碍标签。 */
async function assertChart(page: Page, name: string) {
	const peak = page.locator('[data-drawdown-mark="peak"]');
	const trough = page.locator('[data-drawdown-mark="trough"]');
	if (name === "跨年区间" || name === "同年区间") {
		await check(peak).toHaveCount(1);
		await check(trough).toHaveCount(1);
		await check(peak.locator("title")).toHaveText(/回落高点/);
		await check(trough.locator("title")).toHaveText(/回落低点/);
		const label = await page
			.locator(".price-chart svg")
			.getAttribute("aria-label");
		expect(label).toContain("回落高点");
		expect(label).toContain("回落低点");
		await check(page.locator(".chart-legend")).toContainText("回落高点");
		await check(page.locator(".chart-legend")).toContainText("回落低点");
		// 标记的两天不再画普通圆点。
		const circles = await page.locator(".price-chart svg circle").count();
		const days =
			name === "跨年区间"
				? crossYear.items.filter((i) => i.status === "complete").length
				: sameYear.items.filter((i) => i.status === "complete").length;
		expect(circles).toBe(days - 2);
	} else {
		await check(peak).toHaveCount(0);
		await check(trough).toHaveCount(0);
		await check(page.locator(".chart-legend")).toHaveCount(0);
	}
}
