import { expect } from "@playwright/test";
import { archiveScenarios } from "./analysis-archive.mjs";
export async function cashScenarios(h) {
	const {
		page,
		context,
		api,
		control,
		capture,
		download,
		observations,
		engine,
	} = h;
	await page.getByRole("button", { name: "明细与换汇", exact: true }).click();
	const dialog = page.getByRole("dialog", { name: "现金与换汇", exact: true });
	const cash = dialog.locator(".cash-statement");
	await expect(cash.locator("[data-entry]")).toHaveCount(50);
	await expect(cash.locator('p[role="status"]')).toContainText("共 525 条");
	await expect(cash.locator('[data-cash-currency="CNY"]')).toContainText(
		"990,000.00",
	);
	await expect(cash).toContainText("无关联记录");
	await expect(cash).toContainText("此记录未改变现金");
	for (const width of [1440, 390, 320]) {
		await page.setViewportSize({ width, height: width === 1440 ? 1000 : 844 });
		await cash.getByLabel("资金开始日期").scrollIntoViewIfNeeded();
		await capture(`cash-filters-${width}`);
		await cash.locator(".cash-reconciliation").scrollIntoViewIfNeeded();
		await capture(`cash-reconciliation-${width}`);
	}
	for (let i = 0; i < 14; i++) {
		await page.keyboard.press("Tab");
		expect(
			await page.evaluate(() =>
				Boolean(document.activeElement?.closest("dialog[open]")),
			),
		).toBe(true);
	}
	await capture("cash-keyboard-focus");
	const initial = await api("ledger/page?limit=100");
	const ids = await cash
		.locator("[data-entry]")
		.evaluateAll((rows) => rows.map((row) => row.dataset.entry));
	await control("append");
	for (let index = 1; index <= 10; index++) {
		await cash.getByRole("button", { name: "下一页流水", exact: true }).click();
		await expect(cash.locator('p[role="status"]')).toContainText(
			`第 ${index + 1} 页`,
		);
		ids.push(
			...(await cash
				.locator("[data-entry]")
				.evaluateAll((rows) => rows.map((row) => row.dataset.entry))),
		);
	}
	expect(ids.length).toBe(525);
	expect(new Set(ids).size).toBe(525);
	expect(ids.slice(0, 50)).toEqual(
		initial.items.slice(0, 50).map((row) => row.id),
	);
	await expect(
		cash.getByRole("button", { name: "下一页流水", exact: true }),
	).toBeDisabled();
	observations.push({
		engine,
		name: "pagination-append",
		passed: true,
		total: ids.length,
		unique: new Set(ids).size,
	});
	await capture("cash-page-11-no-duplicate");
	let rows = await download(
		cash.getByRole("button", { name: "导出筛选流水", exact: true }),
		"ledger-all-filtered",
	);
	expect(rows.length).toBe(526);
	expect(rows.some((row) => row["金额"] === "-10000")).toBe(true);
	expect(rows.some((row) => row["说明"].startsWith("'=SUM"))).toBe(true);
	expect(rows.some((row) => row["说明"] === "分页过程中新增记录")).toBe(true);
	await cash.getByRole("button", { name: "刷新到最新流水" }).click();
	await expect(cash.locator('p[role="status"]')).toContainText(
		"共 526 条 · 第 1 页",
	);
	await cash.getByLabel("流水类型").fill("拆股 / 合股");
	await cash.getByRole("button", { name: "应用资金筛选" }).click();
	await expect(cash.locator('p[role="status"]')).toContainText("共 260 条");
	await cash.getByLabel("流水类型").fill("legacy_unknown");
	await cash.getByLabel("资金币种").selectOption("CNY");
	await cash.getByRole("button", { name: "应用资金筛选" }).click();
	await expect(cash.locator('p[role="status"]')).toContainText("共 261 条");
	const cny = cash.locator('[data-cash-currency="CNY"]');
	await expect(cny).toContainText("990,000.00");
	await expect(cny).toContainText("-¥10,000.00");
	expect(await cash.locator("[data-cash-currency]").count()).toBe(1);
	rows = await download(
		cash.getByRole("button", { name: "导出筛选流水", exact: true }),
		"ledger-type-filtered",
	);
	expect(rows.length).toBe(261);
	expect(
		rows.every(
			(row) => row["类型"] === "legacy_unknown" && row["币种"] === "CNY",
		),
	).toBe(true);
	await capture("cash-type-keeps-all-type-balances");
	await cash.getByLabel("流水类型").fill("controlled_cash");
	await cash.getByRole("button", { name: "应用资金筛选" }).click();
	await expect(cash.locator('p[role="status"]')).toContainText("共 1 条");
	await cash.getByRole("button", { name: "查看关联委托", exact: true }).click();
	await expect(cash.locator(".order-row")).toContainText(
		"只用于浏览器关联详情验证",
	);
	await capture("ledger-linked-order");
	await cash.getByLabel("资金开始日期").fill("2026-10-23");
	await cash.getByLabel("资金结束日期").fill("2026-10-21");
	await cash.getByRole("button", { name: "应用资金筛选" }).click();
	await expect(cash.getByRole("alert")).toContainText("开始日期不能晚于");
	await cash.getByLabel("流水类型").fill("");
	await cash.getByLabel("资金开始日期").fill("2027-01-01");
	await cash.getByLabel("资金结束日期").fill("2027-01-02");
	await cash.getByRole("button", { name: "应用资金筛选" }).click();
	await expect(cash).toContainText("此页没有符合条件的流水");
	await capture("cash-empty-date-range");
	await cash.getByRole("button", { name: "清除筛选" }).click();
	await expect(cash.locator("[data-entry]")).toHaveCount(50);
	await page.route("**/api/ledger/export.csv?*", (route) =>
		route.fulfill({ status: 503, json: { detail: "受控导出失败" } }),
	);
	await cash.getByRole("button", { name: "导出筛选流水", exact: true }).click();
	await expect(cash.getByRole("alert")).toContainText("导出失败");
	await capture("cash-export-error");
	await page.unroute("**/api/ledger/export.csv?*");
	await download(
		cash.getByRole("alert").getByRole("button", { name: "重试" }),
		"ledger-retry-recovered",
	);
	let release;
	const delay = new Promise((done) => {
		release = done;
	});
	await page.route("**/api/ledger/page?*", async (route) => {
		await delay;
		await route.continue();
	});
	await cash.getByLabel("流水类型").fill("slow_empty");
	await cash.getByRole("button", { name: "应用资金筛选" }).click();
	await expect(cash.locator(".loading").first()).toContainText("正在同步账户");
	await capture("cash-slow-loading");
	release();
	await expect(cash).toContainText("此页没有符合条件的流水");
	await page.unroute("**/api/ledger/page?*");
	await page.route("**/api/ledger/page?*", (route) =>
		route.fulfill({ status: 503, json: { detail: "受控读取失败" } }),
	);
	await cash.getByLabel("流水类型").fill("error_empty");
	await cash.getByRole("button", { name: "应用资金筛选" }).click();
	await expect(cash.getByRole("alert")).toContainText("受控读取失败", {
		timeout: 30000,
	});
	await capture("cash-query-error");
	await page.unroute("**/api/ledger/page?*");
	await cash.getByRole("alert").getByRole("button", { name: "重试" }).click();
	await expect(cash).toContainText("此页没有符合条件的流水");
	await context.setOffline(true);
	await expect(
		dialog.getByRole("button", { name: "确认换回人民币" }),
	).toBeDisabled();
	await capture("cash-offline");
	await context.setOffline(false);
	await cash.getByRole("button", { name: "清除筛选" }).click();
	await expect(cash.locator("[data-entry]")).toHaveCount(50);
	await control("difference");
	await cash.getByRole("button", { name: "刷新到最新流水" }).click();
	await expect(cash).toContainText("差额 ¥1.00");
	await expect(cash).toContainText("待核对");
	await cash.locator('[data-cash-currency="CNY"]').scrollIntoViewIfNeeded();
	await capture("cash-difference-not-repaired");
	expect((await api("cash-statement")).currencies[0].difference).toBe("1");
	await archiveScenarios(h, (await api("portfolio")).round_id);
}
