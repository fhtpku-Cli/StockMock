import { mkdir, writeFile } from "node:fs/promises";
import { expect } from "@playwright/test";
import react from "@vitejs/plugin-react";
import { createServer } from "vite";
import { launchBrowser } from "./browser.mjs";

const output = new URL(
	"../../.omo/evidence/final-closeout-20261023/volume/",
	import.meta.url,
).pathname;
await mkdir(output, { recursive: true });
const bar = (time, volume) => ({
	time,
	volume,
	open: "10",
	high: "11",
	low: "9",
	close: "10",
});
let history = [
	bar("2026-10-20", "0"),
	bar("2026-10-22", "123456789012345678.125"),
	bar("2026-10-21", "12.345678"),
];
let historyStatus = 200;
const server = await createServer({
	root: new URL("../", import.meta.url).pathname,
	configFile: false,
	plugins: [
		react(),
		{
			name: "controlled-volume-api",
			configureServer(vite) {
				vite.middlewares.use("/api", (request, response) => {
					const route = request.url?.split("?")[0];
					let body;
					if (route?.endsWith("/history")) {
						body = history;
						response.statusCode = historyStatus;
					} else if (route?.endsWith("/quote"))
						body = {
							symbol: "TEST",
							price: "10",
							currency: "CNY",
							market_time: "2026-10-22T07:00:00Z",
							retrieved_at: "2026-10-22T07:15:00Z",
							source: "controlled-test",
							status: "reference",
							message: "受控测试行情",
						};
					else if (route === "/portfolio")
						body = {
							round_id: "test",
							initial_capital: "1000000",
							total_assets_cny: "1000000",
							pnl_cny: "0",
							valuation_status: "complete",
							cash: [],
							positions: [],
							worker: { last_run: null, status: "test" },
							warnings: [],
						};
					else {
						body = { detail: "受控服务没有此路由" };
						response.statusCode = 404;
					}
					response.setHeader("Content-Type", "application/json");
					response.end(JSON.stringify(body));
				});
			},
		},
	],
	server: { host: "127.0.0.1", port: 0 },
});
await server.listen();
const base = server.resolvedUrls?.local[0];
if (!base) throw new Error("隔离受控服务未获得地址");
const browser = await launchBrowser();
const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
const observations = [];
const errors = [];
page.on("pageerror", (error) => errors.push(error.message));
const volume = page.getByRole("region", { name: "日成交量", exact: true });
const capture = async (name) => {
	await page.screenshot({ path: `${output}${name}.png`, fullPage: true });
	observations.push({ name, text: await page.getByRole("dialog").innerText() });
};
try {
	// Given: 乱序日期、零量、小数、大于安全整数的原始数值；When: 打开真实详情组件。
	await page.goto(`${base}tests/volume.html`);
	// Then: 默认日期是最新市场日，大值和小数保持精度。
	await expect(volume.getByRole("combobox")).toHaveValue("2026-10-22");
	await expect(volume.locator("dd")).toHaveText("123,456,789,012,345,678.125");
	await volume.scrollIntoViewIfNeeded();
	const overflow = await page
		.getByRole("dialog")
		.evaluate((node) => node.scrollWidth > node.clientWidth);
	expect(overflow).toBe(false);
	await capture("latest-large-390");
	// Given: 已显示最新日期；When: 选择零量日；Then: 日期及值一起变化。
	await volume.getByRole("combobox").selectOption("2026-10-20");
	await expect(volume.locator("dd")).toHaveText("0");
	await expect(volume.locator(".footnote")).toContainText("2026-10-20");
	await capture("selected-zero");
	// Given: 小数成交量；When: 切换日期；Then: 小数不被千分位拆开。
	await volume.getByRole("combobox").selectOption("2026-10-21");
	await expect(volume.locator("dd")).toHaveText("12.345678");
	await capture("selected-fraction");
	// Given: 仅一个样本；When: 美股详情载入；Then: 可查看单日量且保留市场日期。
	history = [bar("2026-10-20", "1000")];
	await page.goto(`${base}tests/volume.html?market=US`);
	await expect(volume.locator("dd")).toHaveText("1,000");
	await expect(volume.getByRole("combobox")).toHaveValue("2026-10-20");
	await expect(volume.locator(".footnote")).toContainText("新浪日线");
	await expect(page.locator(".price-chart")).toHaveCount(0);
	await capture("single-us-day");
	// Given: 空数据；When: 港股详情载入；Then: 不显示数字或日期选择。
	history = [];
	await page.goto(`${base}tests/volume.html?market=HK`);
	await expect(volume).toBeVisible();
	await expect(volume.locator("dd")).toHaveCount(0);
	await expect(volume.getByRole("combobox")).toHaveCount(0);
	await capture("empty-history");
	// Given: 上游失败；When: 详情重新请求；Then: 提示失败且不显示伪造/缓存量。
	historyStatus = 503;
	await page.reload();
	await expect(page.getByRole("alert")).toBeVisible();
	await expect(volume).toHaveCount(0);
	await capture("failed-history");
	expect(errors).toEqual([]);
	await writeFile(
		`${output}browser.json`,
		JSON.stringify(
			{
				source: "隔离临时端口HTTP受控行情；真实Detail组件和Chromium，非真机",
				invocation: "cd frontend && bun tests/volume.mjs",
				passed: true,
				overflow,
				errors,
				observations,
			},
			null,
			2,
		),
	);
	console.log(
		JSON.stringify({
			passed: true,
			scenarios: observations.map((row) => row.name),
			overflow,
			errors,
		}),
	);
} finally {
	await browser.close();
	await server.close();
}
