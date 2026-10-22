import { launchBrowser } from "./browser.mjs";
import { expect } from "@playwright/test";
import { readFile, writeFile, mkdir } from "node:fs/promises";
const out = new URL(
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
	await page.goto("http://127.0.0.1:8000");
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
	await expect(page.getByText(/已接入 60 只/)).toBeVisible();
	for (const symbol of ["COST", "VOO"]) {
		await page.getByLabel("搜索代码或名称").fill(symbol);
		await expect(page.locator(".security-main")).toHaveCount(1);
		await expect(page.locator(".security-main")).toContainText(symbol);
	}
	await page.locator(".security-main").click();
	await expect(page.getByRole("dialog")).toContainText("证券资料与规则来源");
	await shot("voo-source-label");
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
		catalog: 60,
		searched: ["COST", "VOO"],
		sourceLabel: true,
		errors,
		blockedWrites: writes,
	};
	await writeFile(out + "result.json", JSON.stringify(result, null, 2));
	console.log(JSON.stringify(result));
} finally {
	await browser.close();
}
