import { verifyArchive } from "./completion-checks.mjs";
import { expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { readFile, writeFile } from "node:fs/promises";
import { createCompletionFixture } from "./completion-helpers.mjs";
const {
	out,
	url,
	browser,
	context,
	page,
	errors,
	scenes,
	read,
	shot,
	tick,
	order,
} = await createCompletionFixture();

try {
	await page.goto(url);
	await expect(page).toHaveTitle(/持仓练习/);
	await page
		.getByLabel("访问密码")
		.fill(
			process.env.FRONTEND_TEST_PASSWORD ??
				(
					await readFile(
						new URL("../../backend/.local-password", import.meta.url),
						"utf8",
					)
				).trim(),
		);
	await page.getByRole("button", { name: "进入我的空间" }).click();
	await expect(page.getByRole("heading", { name: "资产概览" })).toBeVisible();
	const originalRound = (await read("/api/portfolio")).round_id;
	const catalog = await read("/api/instruments");
	await page.getByRole("button", { name: "自选", exact: true }).click();
	for (const market of ["CN", "US", "HK"]) {
		await page.getByLabel("筛选市场").selectOption(market);
		await page.getByLabel("筛选证券类型").selectOption("ETF");
		await expect(page.locator(".security-main")).toHaveCount(
			catalog.filter((i) => i.market === market && i.kind === "ETF").length,
		);
	}
	await page.getByLabel("筛选证券类型").selectOption("all");
	await page.getByLabel("筛选交易状态").selectOption("observe");
	await expect(page.locator(".security-main")).toHaveCount(
		catalog.filter((i) => i.market === "HK" && !i.tradable).length,
	);
	await shot("catalog-observation");
	await page.getByLabel("筛选市场").selectOption("US");
	await page.getByLabel("筛选交易状态").selectOption("tradable");
	for (const symbol of ["COST", "VOO"]) {
		const item = catalog.find((i) => i.symbol === symbol);
		expect(item).toBeTruthy();
		for (const term of [item.symbol, item.name]) {
			await page.getByLabel("搜索代码或名称").fill(term);
			await expect(page.locator(".security-main")).toHaveCount(1);
			await expect(page.locator(".security-main")).toContainText(symbol);
		}
	}
	const voo = catalog.find((i) => i.symbol === "VOO");
	await page
		.getByRole("button", { name: `添加自选 ${voo.name}`, exact: true })
		.click();
	await expect(page.locator(".watch-card")).toContainText("VOO");
	await page
		.getByRole("button", { name: `移除自选 ${voo.name}`, exact: true })
		.click();
	await expect(page.locator(".watch-card")).toHaveCount(0);
	await shot("expanded-search-watchlist");

	await order("buy", 10, 100);
	expect((await read("/api/orders"))[0].status).toBe("accepted");
	expect((await read("/api/portfolio")).positions).toHaveLength(0);
	await tick("AAPL", "99");
	await expect(page.locator(".order-row").first()).toContainText("已成交");
	const bought = await read("/api/portfolio");
	expect(bought.positions[0].quantity).toBe(10);
	expect(bought.positions[0].average_cost).toBe("99.05");
	expect(bought.cash.find((c) => c.currency === "USD").balance).toBe("0.00");
	await page.locator(".order-row").first().locator("summary").click();
	await expect(page.locator(".order-row").first()).toContainText(
		"controlled-test",
	);
	await shot("buy-filled-traceability");
	await page.getByRole("button", { name: "资产", exact: true }).click();
	await expect(page.locator("tbody")).toContainText("AAPL");
	await shot("holdings-desktop");
	await page.setViewportSize({ width: 390, height: 844 });
	expect(
		await page.evaluate(
			() => document.documentElement.scrollWidth > innerWidth,
		),
	).toBe(false);
	await shot("holdings-responsive");
	await page.setViewportSize({ width: 1440, height: 1000 });
	await order("sell", 4, 100);
	await tick("AAPL", "101");
	const partialOrders = await read("/api/orders");
	const partial = partialOrders.find((o) => o.side === "sell");
	expect(partial.status).toBe("filled");
	expect(partial.cost_basis).toBe("396.20");
	expect(partial.realized_pnl).toBe("7.60");
	expect((await read("/api/portfolio")).positions[0].quantity).toBe(6);
	await page.locator(".order-row").first().locator("summary").click();
	await expect(page.locator(".order-row").first()).toContainText("US$7.60");
	await expect(page.locator(".order-row").first()).toContainText("US$396.20");
	await expect(page.locator(".order-row").first()).not.toContainText(
		"1 USD = 1 CNY",
	);
	await shot("partial-sale-result");
	await page.route("**/api/orders", async (route) => {
		const response = await route.fetch();
		const rows = await response.json();
		await route.fulfill({
			response,
			json: rows.map((o) => ({ ...o, cost_basis: null, realized_pnl: null })),
		});
	});
	await page.getByRole("button", { name: "刷新数据", exact: true }).click();
	await expect(page.locator(".order-row").first()).toContainText(
		"此历史成交未保存成本结转",
	);
	await shot("legacy-null-result-explicit");
	await page.unroute("**/api/orders");
	await page.getByRole("button", { name: "刷新数据", exact: true }).click();
	await expect(page.locator(".order-row").first()).toContainText("US$7.60");

	await page
		.locator(".order-row")
		.first()
		.getByRole("button", { name: "写复盘" })
		.click();
	await page
		.getByLabel("复盘内容")
		.fill("受控验收复盘：部分卖出已实现7.60美元，剩余6股继续观察。");
	await page.getByRole("button", { name: "保存记录" }).click();
	await expect(page.getByRole("status")).toContainText("已保存");
	await page.getByLabel("关联市场").selectOption("US");
	await expect(page.locator(".journal-list")).toContainText("部分卖出");
	await page.getByLabel("关联市场").selectOption("CN");
	await expect(page.locator(".journal-list article")).toHaveCount(0);
	await page.getByRole("button", { name: "清除筛选" }).click();
	await shot("linked-journal");
	await order("sell", 6, 101);
	await tick("AAPL", "102");
	const sold = await read("/api/portfolio");
	expect(sold.positions).toHaveLength(0);
	expect(sold.cash.find((c) => c.currency === "USD").balance).toBe("1015.49");
	await tick("AAPL", "102", false);
	await order("buy", 1, 90);
	const funded = (await read("/api/orders"))[0];
	expect(funded.fx_source).toBe("");
	expect(funded.fx_time).toBeNull();
	await page.locator(".order-row").first().locator("summary").click();
	await expect(page.locator(".order-row").first()).not.toContainText(
		"1 USD = 1 CNY",
	);
	await expect(page.locator(".order-row").first()).toContainText(
		"使用已有外币现金",
	);
	await shot("funded-buy-no-fx-reference");
	await page
		.locator(".order-row")
		.first()
		.getByRole("button", { name: "申请撤单" })
		.click();
	await tick("AAPL", "102");
	await expect(page.locator(".order-row").first()).toContainText("已撤单");
	await page.getByRole("button", { name: "资产", exact: true }).click();
	await page.getByRole("button", { name: "管理", exact: true }).click();
	await page.getByLabel("换回金额").fill("1015.49");
	await context.setOffline(true);
	await expect(
		page.getByRole("button", { name: "确认换回人民币" }),
	).toBeDisabled();
	await context.setOffline(false);
	await page.getByRole("button", { name: "确认换回人民币" }).click();
	await expect(page.getByRole("status")).toContainText("换汇已完成");
	const converted = await read("/api/portfolio");
	expect(converted.cash.find((c) => c.currency === "USD").balance).toBe("0.00");
	expect(converted.cash.find((c) => c.currency === "CNY").balance).toBe(
		"1000174.93",
	);
	await shot("converted-cash");
	await page.getByRole("button", { name: "关闭", exact: true }).click();
	await page.getByRole("button", { name: "复盘", exact: true }).click();
	const downloadWait = page.waitForEvent("download");
	await page.getByRole("link", { name: "导出资金流水" }).click();
	const download = await downloadWait;
	await download.saveAs(out + "ledger.csv");
	const csv = await readFile(out + "ledger.csv", "utf8");
	expect(csv).toContain("fx_out");
	expect(csv).toContain("sell");
	expect(csv).toContain("fee");
	const audit = await new AxeBuilder({ page }).analyze();
	expect(audit.violations).toEqual([]);
	await page.getByRole("button", { name: /个人账户/ }).click();
	await page.getByLabel("输入“重新开始”以确认").fill("重新开始");
	await page.getByRole("button", { name: "归档本轮，开始新练习" }).click();
	await expect(page.getByRole("dialog")).toHaveCount(0);
	expect((await read("/api/portfolio")).round_id).not.toBe(originalRound);
	expect((await read("/api/portfolio")).total_assets_cny).toBe("1000000.00");
	await page.getByRole("button", { name: /个人账户/ }).click();
	await page.getByRole("button", { name: "查看历史" }).click();
	const archive = page.getByRole("dialog", { name: "已归档练习 · 只读" });
	await expect(archive).toContainText("部分卖出已实现7.60美元");
	await expect(
		archive
			.locator("section")
			.filter({ has: page.getByRole("heading", { name: /历史委托/ }) })
			.locator(".order-row"),
	).toHaveCount(4);
	await expect(
		archive.getByRole("button", { name: /模拟买入|模拟卖出|重开/ }),
	).toHaveCount(0);
	await shot("archived-cycle");
	await verifyArchive({ page, archive, shot });
	expect(errors).toEqual([]);
	await writeFile(
		out + "result.json",
		JSON.stringify(
			{
				source:
					"受控完整交易引擎、临时SQLite、固定美股交易时钟；非真实行情成交",
				catalogCount: catalog.length,
				scenes,
				errors,
				axe: 0,
				partialSale: {
					cost_basis: partial.cost_basis,
					realized_pnl: partial.realized_pnl,
				},
				finalCny: converted.cash.find((c) => c.currency === "CNY").balance,
			},
			null,
			2,
		),
	);
	console.log(
		JSON.stringify({
			scenes: scenes.map((x) => x.name),
			catalog: catalog.length,
			errors,
			axe: 0,
			partialRealized: partial.realized_pnl,
			finalCny: converted.cash.find((c) => c.currency === "CNY").balance,
		}),
	);
} catch (error) {
	await shot("failure");
	throw error;
} finally {
	await browser.close();
}
