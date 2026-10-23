import { expect } from "@playwright/test";
export async function archiveScenarios(h, archivedId) {
	const { page, api, control, capture, download } = h;
	let rows;
	await page
		.getByRole("dialog", { name: "现金与换汇", exact: true })
		.getByRole("button", { name: "关闭", exact: true })
		.click();
	await page.getByRole("button", { name: "复盘", exact: true }).click();
	await expect(page.locator(".cash-statement")).toBeVisible();
	await capture("journal-shared-statement");
	await page.getByRole("button", { name: "账户设置", exact: true }).click();
	await page.getByLabel("输入“重新开始”以确认").fill("重新开始");
	await page.getByRole("button", { name: "归档本轮，开始新练习" }).click();
	await expect(page.getByRole("dialog")).toHaveCount(0);
	await page.getByRole("button", { name: "资产", exact: true }).click();
	await expect(page.getByText("第一笔投资，从观察开始")).toBeVisible();
	await expect(page.locator(".composition")).toContainText("100.00%");
	await capture("new-round-pure-cash");
	for (let i = 0; i < 51; i++) await control("append");
	await page.getByRole("button", { name: "明细与换汇", exact: true }).click();
	const current = page.getByRole("dialog", { name: "现金与换汇", exact: true });
	await current.getByRole("button", { name: "刷新到最新流水" }).click();
	await expect(current.locator('p[role="status"]')).toContainText("共 51 条");
	await current
		.getByRole("button", { name: "下一页流水", exact: true })
		.click();
	await expect(current.locator('p[role="status"]')).toContainText("第 2 页");
	await api("rounds/reset", {
		request_id: "second-context-reset-" + crypto.randomUUID(),
		confirmation: "重新开始",
	});
	await current
		.getByRole("button", { name: "上一页流水", exact: true })
		.click();
	await expect(current.locator('p[role="status"]')).toContainText(
		"共 0 条 · 第 1 页",
	);
	await capture("cash-external-round-change-reloads-first");
	await current.getByRole("button", { name: "关闭", exact: true }).click();

	await page.getByRole("button", { name: "账户设置", exact: true }).click();
	await page
		.getByRole("button", { name: "查看历史", exact: true })
		.first()
		.click();
	const dialog = page.getByRole("dialog", {
		name: "已归档练习 · 只读",
		exact: true,
	});
	const cash = dialog.locator(".cash-statement");
	await expect(cash.locator('p[role="status"]')).toContainText("共 526 条");
	await expect(
		dialog.getByRole("button", { name: "确认换回人民币" }),
	).toHaveCount(0);
	await expect(
		dialog.getByRole("button", { name: "保存复盘", exact: true }),
	).toHaveCount(0);
	await dialog
		.locator(".asset-history")
		.getByRole("button", { name: "近7天", exact: true })
		.click();
	await expect(dialog.locator(".asset-history")).toContainText(
		"当前显示：2026-10-17 至 2026-10-23",
	);
	rows = await download(
		dialog.getByRole("button", { name: "导出所选快照", exact: true }),
		"archive-snapshots",
	);
	expect(rows.every((row) => row["轮次"] === archivedId)).toBe(true);
	rows = await download(
		cash.getByRole("button", { name: "导出筛选流水", exact: true }),
		"archive-ledger",
	);
	expect(rows.length).toBe(526);
	expect(rows.every((row) => row["轮次"] === archivedId)).toBe(true);
	await cash.getByLabel("流水类型").fill("controlled_cash");
	await cash.getByRole("button", { name: "应用资金筛选" }).click();
	await expect(cash.locator('p[role="status"]')).toContainText("共 3 条");
	await cash.getByRole("button", { name: "查看关联委托", exact: true }).click();
	await expect(cash.locator(".order-row")).toContainText(
		"只用于浏览器关联详情验证",
	);
	await capture("archive-linked-order-readonly");
	await cash.getByRole("button", { name: "清除筛选" }).click();
	await expect(cash.locator('p[role="status"]')).toContainText("共 526 条");
	for (const width of [1440, 390, 320]) {
		await page.setViewportSize({ width, height: width === 1440 ? 1000 : 844 });
		await dialog.locator(".asset-history").scrollIntoViewIfNeeded();
		await capture(`archive-history-${width}`);
		await cash.getByLabel("资金开始日期").scrollIntoViewIfNeeded();
		await capture(`archive-cash-${width}`);
	}
}
