import { expect } from "@playwright/test";
export async function assetScenarios(h) {
	const { page, api, control, capture, download } = h;
	const portfolio = await api("portfolio");
	await expect(page.locator(".composition")).toContainText("15,000.00");
	await expect(page.locator(".composition")).toContainText("140.00");
	await expect(page.locator(".positions-table tbody tr")).toHaveCount(3);
	expect(
		await page
			.locator("[data-position]")
			.evaluateAll((rows) => rows.map((row) => row.dataset.position)),
	).toEqual(["AAPL", "MSFT", "510300.SS"]);
	await expect(page.locator('[data-position="AAPL"]')).toContainText(
		"参考估值",
	);
	await page.getByLabel("持仓市场").selectOption("US");
	await expect(page.locator("[data-position]")).toHaveCount(2);
	await expect(page.locator('[data-position="AAPL"]')).toContainText("0.70%");
	await page.getByLabel("持仓币种").selectOption("CNY");
	await expect(page.getByText("没有符合筛选条件的持仓。")).toBeVisible();
	await capture("position-empty-filter");
	await page.getByLabel("持仓币种").selectOption("");
	await page.getByLabel("持仓市场").selectOption("");
	for (const sort of ["weight_percent", "pnl_percent", "market_value_cny"]) {
		await page.getByLabel("持仓排序").selectOption(sort);
		expect(
			await page
				.locator("[data-position]")
				.first()
				.getAttribute("data-position"),
		).toBe("AAPL");
	}
	for (const width of [1440, 390, 320]) {
		await page.setViewportSize({ width, height: width === 1440 ? 1000 : 844 });
		await page.locator(".composition h2").scrollIntoViewIfNeeded();
		await capture(`composition-${width}`);
		await page.locator(".composition details").scrollIntoViewIfNeeded();
		await capture(`composition-end-${width}`);
		await page.getByLabel("持仓排序").scrollIntoViewIfNeeded();
		await capture(`positions-${width}`);
	}
	await page.locator('[data-position="AAPL"] .symbol-link').click();
	await expect(page.getByRole("dialog")).toContainText("苹果");
	await capture("position-detail-320");
	await page
		.getByRole("dialog")
		.getByRole("button", { name: "关闭", exact: true })
		.click();
	await control("fx");
	await page.getByRole("button", { name: "刷新数据", exact: true }).click();
	await expect(page.locator(".composition")).toContainText("外汇来源不可用");
	await expect(page.locator('[data-position="AAPL"]')).toContainText(
		"1,000.00",
	);
	await expect(page.locator('[data-position="AAPL"]')).toContainText(
		"占总资产 未知",
	);
	expect(
		await page.locator("[data-position]").first().getAttribute("data-position"),
	).toBe("510300.SS");
	await page.locator(".composition").scrollIntoViewIfNeeded();
	await capture("fx-missing-native-value-retained");
	await control("quote");
	await page.getByRole("button", { name: "刷新数据", exact: true }).click();
	await expect(page.locator('[data-position="MSFT"]')).toContainText(
		"人民币估值缺失",
	);
	expect(
		await page.locator("[data-position]").last().getAttribute("data-position"),
	).toBe("MSFT");
	await control("normal");
	await page.getByRole("button", { name: "刷新数据", exact: true }).click();
	await expect(page.locator('[data-position="MSFT"]')).toContainText(
		"7,000.00",
	);
	const history = page.locator(".asset-history");
	for (const [label, start] of [
		["近7天", "2026-10-17"],
		["近30天", "2026-09-24"],
		["近90天", "2026-07-26"],
		["本轮全部", "2026-10-01"],
	]) {
		await history.getByRole("button", { name: label, exact: true }).click();
		await expect(history).toContainText(`当前显示：${start} 至 2026-10-23`);
	}
	await expect(history.locator(".history-summary")).toContainText("+¥1,200.00");
	await expect(history.locator('[data-testid="asset-segment"]')).toHaveCount(3);
	await history.getByRole("button", { name: "自定日期", exact: true }).click();
	const custom = async (start, end) => {
		await history.getByLabel("轨迹开始日期").fill(start);
		await history.getByLabel("轨迹结束日期").fill(end);
		await history.getByRole("button", { name: "应用轨迹日期" }).click();
	};
	await custom("2026-10-23", "2026-10-21");
	await expect(history.getByRole("alert")).toContainText("开始日期不能晚于");
	await custom("2026-10-21", "2026-10-23");
	await expect(history).toContainText("实际记录 2 天");
	await expect(history).toContainText("未记录 1 天");
	let rows = await download(
		history.getByRole("button", { name: "导出所选快照", exact: true }),
		"snapshot-selected",
	);
	expect(rows.length).toBe(2);
	expect(rows.at(-1)["人民币总资产"]).toBe("1001200");
	for (const width of [1440, 390, 320]) {
		await page.setViewportSize({ width, height: width === 1440 ? 1000 : 844 });
		await history.scrollIntoViewIfNeeded();
		await capture(`history-custom-${width}`);
	}
	await custom("2026-10-22", "2026-10-22");
	await expect(history).toContainText("所选范围没有实际资产记录");
	await capture("history-no-records");
	await custom("2026-10-21", "2026-10-21");
	await expect(history).toContainText("只有一个记录日");
	await expect(history.locator(".history-summary > strong")).toHaveText("—");
	await control("endpoint");
	await custom("2026-10-21", "2026-10-23");
	await page.getByRole("button", { name: "刷新数据", exact: true }).click();
	await expect(history).toContainText("实际首末记录估值不完整");
	await expect(history.locator(".history-summary > strong")).toHaveText("—");
	rows = await download(
		history.getByRole("button", { name: "导出所选快照", exact: true }),
		"snapshot-missing-endpoint",
	);
	expect(rows.at(-1)["人民币总资产"]).toBe("");
	expect(rows.at(-1)["状态"]).toBe("missing");
	await capture("history-missing-endpoint");
	const before = await api("portfolio");
	expect(before.cash).toEqual(portfolio.cash);
	expect(before.positions).toEqual(portfolio.positions);
}
