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
page.setDefaultTimeout(15000);
const portfolio = {
	round_id: "precision-test",
	initial_capital: "1000000.00",
	total_assets_cny: "999999.79",
	pnl_cny: "-0.21",
	valuation_status: "受控显示测试",
	cash: [
		{
			currency: "CNY",
			balance: "999580.89",
			reserved: "0",
			available: "999580.89",
		},
	],
	positions: [
		{
			symbol: "510300.SS",
			name: "沪深300ETF",
			currency: "CNY",
			quantity: 100,
			sellable: 100,
			reserved: 0,
			average_cost: "4.1911",
			market_price: "4.189",
			market_value: "418.90",
			pnl: "-0.21",
			valuation_status: "受控数据，仅验证含费单位成本精度",
		},
	],
	worker: { last_run: null, status: "starting" },
	warnings: ["受控界面测试，非真实账户数据；不写入服务端。"],
};
try {
	await page.route("**/api/portfolio", (route) =>
		route.fulfill({ json: portfolio }),
	);
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
	await expect(page.locator("tbody tr td").nth(2)).toContainText("4.1911");
	await page.screenshot({
		animations: "disabled",
		path: output + "controlled-position-precision.png",
	});
	await writeFile(
		output + "position-precision.json",
		JSON.stringify(
			{
				source:
					"浏览器专用受控返回，只验证持仓显示；不修改生产服务、数据或 fallback",
				inputAverageCost: portfolio.positions[0].average_cost,
				display: await page.locator("tbody tr td").nth(2).innerText(),
				result: "通过",
			},
			null,
			2,
		),
	);
	console.log("单位成本4.1911显示：通过");
} finally {
	await browser.close();
}
