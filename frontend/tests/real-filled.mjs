import { launchBrowser } from "./browser.mjs";
import { expect } from "@playwright/test";
import { readFile, writeFile } from "node:fs/promises";
const out = new URL(
	"../../.omo/evidence/completion-20261023/ui/",
	import.meta.url,
).pathname;
const browser = await launchBrowser();
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
const errors = [];
const sold = process.env.REAL_REVIEW_PHASE === "sold";
const prefix = sold ? "real-sold" : "real-filled";
page.on("pageerror", (e) => errors.push(e.message));
try {
	await page.goto("http://127.0.0.1:8005");
	await page
		.getByLabel("访问密码")
		.fill(
			(
				await readFile(
					new URL("../../backend/.local-password", import.meta.url),
					"utf8",
				)
			).trim(),
		);
	await page.getByRole("button", { name: "进入我的空间" }).click();
	await expect(page.getByRole("heading", { name: "资产概览" })).toBeVisible();
	if (sold) await expect(page.locator(".symbol-link")).toHaveCount(0);
	else await expect(page.locator(".symbol-link")).toContainText("AAPL");
	await page.screenshot({
		path: out + prefix + "-holdings.png",
		fullPage: true,
		animations: "disabled",
	});
	const orders = await (
		await page.request.get("http://127.0.0.1:8005/api/orders")
	).json();
	const buy = orders.find(
		(o) => o.side === (sold ? "sell" : "buy") && o.status === "filled",
	);
	expect(buy).toBeTruthy();
	expect(buy.source).toContain("sina");
	await page.getByRole("button", { name: "委托", exact: true }).click();
	const row = page
		.locator(".order-row")
		.filter({ hasText: sold ? "卖出 1 股" : "买入 1 股" });
	await expect(row).toContainText("已成交");
	await row.locator("summary").click();
	await expect(row).toContainText(buy.fill_price);
	await expect(row).toContainText(buy.fee);
	await expect(row).toContainText(buy.source);
	if (sold) {
		expect(buy.realized_pnl).not.toBeNull();
		await expect(row).toContainText(buy.cost_basis);
		await expect(row).toContainText(
			new Intl.NumberFormat("zh-CN", {
				style: "currency",
				currency: buy.currency,
			}).format(Number(buy.realized_pnl)),
		);
	}
	await row.screenshot({
		path: out + prefix + "-order.png",
		animations: "disabled",
	});
	await page.setViewportSize({ width: 390, height: 844 });
	expect(
		await page.evaluate(
			() => document.documentElement.scrollWidth > innerWidth,
		),
	).toBe(false);
	await page.screenshot({
		path: out + prefix + "-responsive.png",
		fullPage: true,
		animations: "disabled",
	});
	expect(errors).toEqual([]);
	const result = {
		source: "真实美股成交隔离账本的只读SQLite副本；原始数据库未改写",
		symbol: buy.symbol,
		side: buy.side,
		cost_basis: buy.cost_basis,
		realized_pnl: buy.realized_pnl,
		filled_at: buy.filled_at,
		confirmed_at: buy.confirmed_at,
		price: buy.fill_price,
		fee: buy.fee,
		sampleSource: buy.source,
		errors,
		views: 3,
	};
	await writeFile(out + prefix + ".json", JSON.stringify(result, null, 2));
	console.log(JSON.stringify(result));
} finally {
	await browser.close();
}
