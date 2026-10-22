import { mkdir, writeFile } from "node:fs/promises";
import { expect as baseExpect } from "@playwright/test";
import { launchBrowser } from "./browser.mjs";

const expect = baseExpect.configure({ timeout: 60000 });
const baseUrl = process.env.DISCOVERY_TEST_URL ?? "http://127.0.0.1:5191";
const output = new URL(
	"../../.omo/evidence/market-discovery-ui/",
	import.meta.url,
).pathname;
await mkdir(output, { recursive: true });
const browser = await launchBrowser();
const context = await browser.newContext({
	viewport: { width: 1280, height: 1000 },
});
const page = await context.newPage();
page.setDefaultTimeout(60000);
const results = [];
const pageErrors = [];
page.on("pageerror", (error) => pageErrors.push(error.message));
const capture = async (name) => {
	await page.screenshot({ path: `${output}${name}.png`, fullPage: true });
	expect(
		await page.evaluate(
			() => document.documentElement.scrollWidth > innerWidth,
		),
	).toBe(false);
	results.push({
		scenario: name,
		observable: "页面截图已生成，无横向溢出",
		artifact: `${name}.png`,
	});
};
const enterDiscovery = async () => {
	await page.getByRole("button", { name: "自选", exact: true }).click();
	await page.getByLabel("搜索代码或名称").waitFor();
};
const search = async (query) => {
	const response = page.waitForResponse((response) => {
		const url = new URL(response.url());
		return (
			url.pathname === "/api/instruments/search" &&
			url.searchParams.get("q") === query
		);
	});
	await page.getByLabel("搜索代码或名称").fill(query);
	const result = await response;
	expect(result.status()).toBe(200);
	return result.json();
};
try {
	await page.goto(baseUrl);
	await page.getByLabel("访问密码").fill("discovery-local-test-only");
	await page.getByRole("button", { name: "进入我的空间" }).click();
	await page.getByRole("heading", { name: "资产概览" }).waitFor();
	const catalog = await (
		await context.request.get(`${baseUrl}/api/instruments`)
	).json();
	expect(Array.isArray(catalog)).toBe(true);
	const existingWatch = await (
		await context.request.get(`${baseUrl}/api/watchlist`)
	).json();
	for (const item of existingWatch)
		await context.request.delete(
			`${baseUrl}/api/watchlist/${encodeURIComponent(item.symbol)}`,
		);
	await enterDiscovery();
	await expect(page.locator(".security-row")).toHaveCount(catalog.length);
	await capture("common-catalog");
	let blankRequests = 0;
	page.on("request", (request) => {
		const url = new URL(request.url());
		if (
			url.pathname === "/api/instruments/search" &&
			!url.searchParams.get("q")?.trim()
		)
			blankRequests += 1;
	});
	await page.getByLabel("搜索代码或名称").fill("   ");
	await expect(page.locator(".security-row")).toHaveCount(catalog.length);
	for (const [query, symbol] of [
		["IBM", "IBM"],
		["688981", "688981.SS"],
		["00981", "00981.HK"],
		["512660", "512660.SS"],
	]) {
		expect(catalog.some((item) => item.symbol === symbol)).toBe(false);
		const found = await search(query);
		const item = found.items.find((item) => item.symbol === symbol);
		expect(item).toBeDefined();
		expect(item.tradable).toBe(false);
		expect(item.lot_size).toBe(null);
		expect(item.tick_size).toBe(null);
		await page
			.locator(".security-main")
			.filter({ hasText: symbol })
			.first()
			.click();
		const dialog = page.getByRole("dialog");
		await expect(dialog.locator(".loading")).toHaveCount(0);
		await expect(dialog.locator(".quote-price")).toBeVisible();
		await expect(dialog.locator(".price-chart")).toBeVisible();
		await expect(
			dialog.getByRole("button", { name: "模拟买入", exact: true }),
		).toBeDisabled();
		await expect(
			dialog.getByRole("button", { name: "模拟卖出", exact: true }),
		).toBeDisabled();
		await expect(
			dialog.locator(".summary-list").filter({ hasText: "每手数量" }),
		).toContainText("待核验");
		await capture(`live-detail-${symbol}`);
		await dialog.locator(".discovery-detail-actions").scrollIntoViewIfNeeded();
		await capture(`live-detail-actions-${symbol}`);
		await dialog.getByRole("button", { name: "加入自选", exact: true }).click();
		await expect(
			dialog.getByRole("button", { name: "移除自选", exact: true }),
		).toBeEnabled();
		await page.keyboard.press("Escape");
		await page.reload();
		await enterDiscovery();
		const saved = page.locator(".watch-card").filter({ hasText: symbol });
		await expect(saved).toHaveCount(1);
		await saved.click();
		await page
			.getByRole("dialog")
			.getByRole("button", { name: "移除自选", exact: true })
			.click();
		await expect(
			page
				.getByRole("dialog")
				.getByRole("button", { name: "加入自选", exact: true }),
		).toBeEnabled();
		await page.keyboard.press("Escape");
		await expect(
			page.locator(".watch-card").filter({ hasText: symbol }),
		).toHaveCount(0);
		results.push({
			scenario: `真实在线搜索→详情→自选刷新保留→移除 ${symbol}`,
			source: found.source,
			outsideCatalog: true,
			tradable: item.tradable,
			lot_size: item.lot_size,
			tick_size: item.tick_size,
			artifact: `live-detail-${symbol}.png`,
		});
	}
	const chinese = await search("中芯国际");
	expect(chinese.items.some((item) => item.symbol === "688981.SS")).toBe(true);
	await page.getByLabel("筛选市场").selectOption("CN");
	await expect(page.locator(".security-row")).not.toHaveCount(0);
	for (const text of await page.locator(".security-main").allTextContents())
		expect(text).toContain("CN");
	await capture("chinese-market-filter");
	await page.getByLabel("筛选市场").selectOption("all");
	await search("512660");
	await page.getByLabel("筛选证券类型").selectOption("ETF");
	await expect(page.locator(".security-row")).toHaveCount(1);
	await page.getByLabel("筛选交易状态").selectOption("tradable");
	await expect(page.locator(".security-row")).toHaveCount(0);
	await page.getByLabel("筛选交易状态").selectOption("observe");
	await expect(page.locator(".security-row")).toHaveCount(1);
	await page.getByLabel("筛选证券类型").selectOption("all");
	await page.getByLabel("筛选交易状态").selectOption("all");
	await search("AAPL");
	await page
		.locator(".security-main")
		.filter({ hasText: "AAPL" })
		.first()
		.click();
	await expect(
		page
			.getByRole("dialog")
			.getByRole("button", { name: "模拟买入", exact: true }),
	).toBeEnabled();
	await page
		.getByRole("dialog")
		.getByRole("button", { name: "模拟买入", exact: true })
		.click();
	await expect(page.getByLabel(/数量（每手/)).toHaveValue("1");
	await expect(page.getByLabel("限价（USD）")).toHaveAttribute("min", "0.01");
	await capture("known-tradable-ticket");
	await page.keyboard.press("Escape");
	await page.route("**/api/instruments/search?q=ADBE", (route) =>
		route.fulfill({
			status: 503,
			contentType: "application/json",
			body: JSON.stringify({
				detail: { code: "test_failure", message: "受控搜索故障" },
			}),
		}),
	);
	await page.getByLabel("搜索代码或名称").fill("ADBE");
	await expect(page.locator(".discovery").getByRole("alert")).toContainText(
		"受控搜索故障",
	);
	await capture("controlled-search-error");
	await page.unroute("**/api/instruments/search?q=ADBE");
	await page
		.locator(".discovery")
		.getByRole("button", { name: "重试", exact: true })
		.click();
	await expect(
		page.locator(".security-main").filter({ hasText: "ADBE" }),
	).toHaveCount(1);
	await capture("search-recovered-live");
	const observation = {
		symbol: "RACEB",
		name: "International Very Long Company Name For Mobile Search Layout Verification",
		market: "US",
		currency: "USD",
		kind: "stock",
		tradable: false,
		restriction: "受控布局与搜索竞态测试，不是真实行情",
		lot_size: null,
		tick_size: null,
		tick_rule: null,
	};
	let releaseOld;
	const oldGate = new Promise((resolve) => {
		releaseOld = resolve;
	});
	await page.route("**/api/instruments/search?q=RACEA", async (route) => {
		await oldGate;
		await route.fulfill({
			json: {
				items: [{ ...observation, symbol: "RACEA", name: "旧响应" }],
				source: "controlled-ui-test",
				message: "受控响应顺序",
			},
		});
	});
	await page.route("**/api/instruments/search?q=RACEB", (route) =>
		route.fulfill({
			json: {
				items: [observation],
				source: "controlled-ui-test",
				message: "受控响应顺序",
			},
		}),
	);
	const oldRequest = page.waitForRequest("**/api/instruments/search?q=RACEA");
	await page.getByLabel("搜索代码或名称").fill("RACEA");
	await oldRequest;
	await search("RACEB");
	await expect(page.locator(".security-main")).toContainText("RACEB");
	const oldResponse = page.waitForResponse("**/api/instruments/search?q=RACEA");
	releaseOld();
	await oldResponse;
	await expect(page.locator(".security-main")).toContainText("RACEB");
	await expect(page.locator(".security-main")).not.toContainText("RACEA");
	for (const width of [320, 390]) {
		await page.setViewportSize({ width, height: 844 });
		await page.locator(".security-main").scrollIntoViewIfNeeded();
		await capture(`controlled-long-name-${width}`);
		expect(
			await page
				.locator(".security-row")
				.evaluate((element) => element.scrollWidth <= element.clientWidth),
		).toBe(true);
		for (const locator of [
			page.getByLabel("搜索代码或名称"),
			page.getByLabel("筛选市场"),
			page.locator(".security-main"),
			page.getByRole("button", { name: `添加自选 ${observation.name}` }),
		]) {
			const box = await locator.boundingBox();
			expect(box.width).toBeGreaterThan(40);
			expect(box.x + box.width).toBeLessThanOrEqual(width);
		}
	}
	expect(blankRequests).toBe(0);
	expect(pageErrors).toEqual([]);
	results.push({
		scenario: "空白输入与竞态",
		blankRequests,
		currentResult: "RACEB",
		oldResultAbsent: true,
	});
	await writeFile(
		`${output}results.json`,
		JSON.stringify(
			{
				passed: true,
				baseUrl,
				dataBoundary:
					"独立测试SQLite；真实外部搜索与报价；仅故障和竞态/长名场景使用显式受控HTTP响应",
				results,
				pageErrors,
			},
			null,
			2,
		),
	);
	console.log(
		JSON.stringify({ passed: true, scenarios: results.length, output }),
	);
} catch (error) {
	await page.screenshot({ path: `${output}failure.png`, fullPage: true });
	await writeFile(
		`${output}failure.json`,
		JSON.stringify({ message: String(error), results, pageErrors }, null, 2),
	);
	throw error;
} finally {
	await context.close();
	await browser.close();
}
