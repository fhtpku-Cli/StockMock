import { beforeAll, expect, test } from "bun:test";
import { chromium, expect as expectBrowser } from "@playwright/test";
import { existsSync } from "node:fs";
import { CANCEL_CONFIRM } from "../src/Orders";
import { orderSchema } from "../src/schema";

let fixture = "";
beforeAll(async () => {
	const result = await Bun.build({
		entrypoints: [
			new URL("./orders-confirm-fixture.tsx", import.meta.url).pathname,
		],
		target: "browser",
		format: "iife",
		define: { "process.env.NODE_ENV": JSON.stringify("production") },
	});
	if (!result.success || !result.outputs[0])
		throw new Error("撤单行为夹具构建失败");
	fixture = await result.outputs[0].text();
});

const accepted = orderSchema.parse({
	id: "cancel-confirm",
	request_id: "cancel-confirm-request",
	symbol: "AAPL",
	side: "buy",
	order_type: "limit",
	quantity: 1,
	limit_price: "100",
	price_bound: "100",
	status: "accepted",
	status_reason: "",
	created_at: "2026-10-30T14:00:00Z",
	active_at: "2026-10-30T14:00:00Z",
	expires_at: "2026-10-30T20:00:00Z",
	cancel_at: null,
	filled_at: null,
	confirmed_at: null,
	fill_price: null,
	fee: null,
	currency: "USD",
	reserved_cny: "700",
	reserved_foreign: "0",
	fx_rate: "7",
	fx_source: "受控数据",
	fx_time: "2026-10-30T14:00:00Z",
	reason: "受控撤单确认测试",
	target_price: null,
	stop_price: null,
});

test.each([false, true])(
	"撤单确认框选择 %p 时请求次数正确且文案等于 CANCEL_CONFIRM",
	async (confirmed) => {
		const systemChrome = [
			"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
			"/usr/bin/google-chrome",
		].find((path) => existsSync(path));
		const executablePath = process.env.CHROME_PATH ?? systemChrome;
		const browser = await chromium.launch({
			...(executablePath ? { executablePath } : {}),
			headless: true,
		});
		const page = await browser.newPage();
		let requests = 0;
		let current = accepted;
		const messages: string[] = [];
		try {
			// Given：真实 Orders 组件；所有请求在浏览器中受控，不连接账户服务。
			await page.route("**/*", async (route) => {
				const path = new URL(route.request().url()).pathname;
				if (
					path === "/api/orders/cancel-confirm/cancel" &&
					route.request().method() === "POST"
				) {
					requests += 1;
					current = { ...accepted, status: "cancel_pending" };
					await route.fulfill({ json: current });
				} else if (path === "/api/orders")
					await route.fulfill({ json: [current] });
				else if (path === "/")
					await route.fulfill({
						contentType: "text/html",
						body: '<div id="root"></div>',
					});
				else await route.abort();
			});
			await page.goto("http://stockmock-confirm.test/");
			await page.addScriptTag({ content: fixture });
			const button = page.getByRole("button", { name: "申请撤单" });
			await expectBrowser(button).toBeEnabled();
			page.once("dialog", async (dialog) => {
				messages.push(dialog.message());
				if (confirmed) await dialog.accept();
				else await dialog.dismiss();
			});
			// When：用户真实点击并选择确认框分支。
			await button.click();
			// Then：确认后等待请求回执落在页面；取消后按钮仍可用。
			if (confirmed)
				await expectBrowser(page.locator(".order-row")).toContainText(
					"撤单确认中",
				);
			else await expectBrowser(button).toBeEnabled();
			expect(messages).toEqual([CANCEL_CONFIRM]);
			expect(requests).toBe(confirmed ? 1 : 0);
		} finally {
			await browser.close();
		}
	},
	20000,
);
