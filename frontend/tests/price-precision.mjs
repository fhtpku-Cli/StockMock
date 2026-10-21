import { expect } from "@playwright/test";
import { readFile, writeFile } from "node:fs/promises";
import { launchBrowser, baseUrl } from "./browser.mjs";
const output = new URL("../../.omo/evidence/frontend/", import.meta.url)
	.pathname;
const browser = await launchBrowser();
const context = await browser.newContext({
	viewport: { width: 1440, height: 1000 },
});
const page = await context.newPage();
page.setDefaultTimeout(30000);
try {
	await page.goto(baseUrl);
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
	await page.getByRole("heading", { name: "资产概览" }).waitFor();
	await page.getByRole("button", { name: "自选", exact: true }).click();
	await page
		.locator(".security-main")
		.filter({ hasText: "沪深300ETF" })
		.click();
	await expect(
		page.getByRole("button", { name: "模拟买入", exact: true }),
	).toBeEnabled();
	const quote = await (
		await page.request.get(baseUrl + "/api/instruments/510300.SS/quote")
	).json();
	const expected = Number(quote.price).toFixed(3);
	await expect(page.locator(".quote-price")).toContainText(expected);
	await page.screenshot({
		animations: "disabled",
		path: output + "etf-quote-precision.png",
	});
	await page.getByRole("button", { name: "模拟买入", exact: true }).click();
	await page.getByLabel("限价（CNY）").fill(quote.price);
	await page.getByRole("button", { name: "预览费用与资金占用" }).click();
	await page.getByRole("heading", { name: "确认这笔委托" }).waitFor();
	await expect(page.locator(".preview-box")).toContainText(expected);
	await page.screenshot({
		animations: "disabled",
		path: output + "etf-preview-precision.png",
	});
	await writeFile(
		output + "price-precision.json",
		JSON.stringify(
			{
				symbol: "510300.SS",
				source: "真实行情，仅预览不提交",
				quote,
				displayedPrice: expected,
				previewText: await page.locator(".preview-box").innerText(),
				result: "通过",
			},
			null,
			2,
		),
	);
	console.log("ETF 0.001 报价与预览精度：通过");
} finally {
	await browser.close();
}
