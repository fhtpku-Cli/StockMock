import { mkdir, readFile, writeFile } from "node:fs/promises";
import { expect as baseExpect } from "@playwright/test";
import { launchBrowser } from "./browser.mjs";

const expect = baseExpect.configure({ timeout: 60000 });
const out = new URL(
	process.env.FRONTEND_TEST_OUTPUT ??
		"../../.omo/evidence/completion-20261023/ui/production/",
	import.meta.url,
).pathname;
await mkdir(out, { recursive: true });
const browser = await launchBrowser();
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
const errors = [],
	writes = [],
	views = [];
page.on("pageerror", (e) => errors.push(e.message));
await page.route("**/api/**", async (route) => {
	const request = route.request();
	if (
		!["GET", "HEAD"].includes(request.method()) &&
		!request.url().endsWith("/api/auth/login")
	) {
		writes.push(request.method() + " " + new URL(request.url()).pathname);
		await route.abort();
	} else await route.continue();
});
const shot = async (name) => {
	await page.screenshot({ path: out + name + ".png", animations: "disabled" });
	views.push(name);
};
try {
	await page.goto(process.env.FRONTEND_TEST_URL ?? "http://127.0.0.1:8000");
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
	await expect(page.locator(".asset-value")).not.toHaveText("—");
	await shot("assets-desktop");
	for (const name of ["委托", "复盘", "自选"]) {
		await page.getByRole("button", { name, exact: true }).click();
		await expect(
			page.getByRole("heading", { name, exact: true }),
		).toBeVisible();
	}
	const catalogResponse = await page
		.context()
		.request.get(new URL("/api/instruments", page.url()).href);
	const catalog = await catalogResponse.json();
	expect(catalog.length).toBeGreaterThanOrEqual(60);
	await expect(page.locator(".security-main")).toHaveCount(catalog.length);
	for (const symbol of ["COST", "VOO"]) {
		await page.getByLabel("搜索代码或名称").fill(symbol);
		const result = page.locator(`[data-symbol="${symbol}"] .security-main`);
		await expect(result).toHaveCount(1);
		await expect(result).toContainText(symbol);
	}
	const historyResponse = page.waitForResponse(
		(response) =>
			response.url().includes("/api/instruments/VOO/history") &&
			response.request().method() === "GET",
	);
	await page.locator('[data-symbol="VOO"] .security-main').click();
	await expect(page.getByRole("dialog")).toContainText("证券资料与规则来源");
	await shot("voo-source-label");
	const history = await (await historyResponse).json();
	expect(Array.isArray(history)).toBe(true);
	expect(history.length).toBeGreaterThan(1);
	const days = [...history].sort((a, b) => a.time.localeCompare(b.time));
	const latest = days.at(-1);
	const earlier = days.find(
		(row) =>
			row.time !== latest.time && String(row.volume) !== String(latest.volume),
	);
	expect(earlier).toBeTruthy();
	const volume = page.getByRole("region", { name: "日成交量", exact: true });
	const selection = volume.getByLabel(/市场日期/);
	await expect(selection).toHaveValue(latest.time);
	expect((await volume.locator("dd").innerText()).replaceAll(",", "")).toBe(
		String(latest.volume),
	);
	await shot("voo-volume-latest");
	await selection.selectOption(earlier.time);
	await expect(selection).toHaveValue(earlier.time);
	expect((await volume.locator("dd").innerText()).replaceAll(",", "")).toBe(
		String(earlier.volume),
	);
	await shot("voo-volume-earlier");
	await writeFile(
		out + "volume-response.json",
		JSON.stringify({ latest, earlier, count: days.length }, null, 2),
	);
	await page.getByRole("button", { name: "关闭", exact: true }).click();
	await page.setViewportSize({ width: 390, height: 844 });
	await page.getByRole("button", { name: "资产", exact: true }).click();
	await expect(page.locator(".asset-value")).not.toHaveText("—");
	expect(
		await page.evaluate(
			() => document.documentElement.scrollWidth > innerWidth,
		),
	).toBe(false);
	await shot("assets-responsive");
	expect(errors).toEqual([]);
	expect(writes).toEqual([]);
	const result = {
		source: "部署后真实服务只读浏览器；只允许登录写请求",
		views,
		catalog: catalog.length,
		searched: ["COST", "VOO"],
		sourceLabel: true,
		volumeDatesVerified: true,
		errors,
		blockedWrites: writes,
	};
	await writeFile(out + "result.json", JSON.stringify(result, null, 2));
	console.log(JSON.stringify(result));
} finally {
	await browser.close();
}
