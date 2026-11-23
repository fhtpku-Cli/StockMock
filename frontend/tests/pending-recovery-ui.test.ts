import { expect, test } from "bun:test";
import { existsSync } from "node:fs";
import { chromium, expect as expectPage } from "@playwright/test";
import { PENDING_ORDER_KEY, type Submission } from "../src/pendingOrder";

test("刷新和重登只核对结果，明确重试才发送原委托", async () => {
	const entry = new URL("../src/main.tsx", import.meta.url).pathname;
	const build = await Bun.build({
		entrypoints: [entry],
		target: "browser",
		format: "iife",
		define: {
			"import.meta.env.DEV": "false",
			"process.env.NODE_ENV": '"production"',
		},
	});
	const script = build.outputs.find((output) => output.path.endsWith(".js"));
	if (!build.success || !script) throw new Error("应用入口构建失败");
	const bundle = await script.text();
	const executablePath =
		process.env.CHROME_PATH ??
		[
			"/usr/bin/google-chrome",
			"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
		].find((path) => existsSync(path));
	const browser = await chromium.launch(
		executablePath ? { executablePath } : {},
	);
	const page = await browser.newPage();
	const saved: Submission = {
		request_id: "original-request",
		expected_round_id: "original-round",
		expected_expires_at: "2026-11-20T07:00:00Z",
		symbol: "600519.SS",
		side: "buy",
		order_type: "limit",
		quantity: 100,
		limit_price: "100",
		reason: "保留原交易理由",
		save_as_plan: true,
		plan_review_date: "2026-11-24",
	};
	let authenticated = false;
	let reads = 0;
	const submissions: unknown[] = [];
	const retryHeaders: (string | null)[] = [];
	try {
		await page.route("**/*", async (route) => {
			const path = new URL(route.request().url()).pathname;
			if (path === "/")
				return route.fulfill({
					contentType: "text/html; charset=utf-8",
					body: '<div id="root"></div><script src="/app.js"></script>',
				});
			if (path === "/app.js")
				return route.fulfill({
					contentType: "application/javascript; charset=utf-8",
					body: bundle,
				});
			if (path === "/api/auth/session")
				return route.fulfill({ json: { authenticated, configured: true } });
			if (path === "/api/auth/login") {
				authenticated = true;
				return route.fulfill({ json: {} });
			}
			if (path === "/api/orders/receipts/original-request") {
				reads++;
				if (submissions.length > 0)
					return route.fulfill({
						status: 409,
						json: {
							detail: {
								code: "order_receipt_rejected",
								message: "原轮次已归档",
							},
						},
					});
				return route.fulfill({
					status: 404,
					json: {
						detail: { code: "order_receipt_missing", message: "暂未查到" },
					},
				});
			}
			if (path === "/api/orders" && route.request().method() === "POST") {
				submissions.push(route.request().postDataJSON());
				retryHeaders.push(
					await route.request().headerValue("X-StockMock-Retry"),
				);
				return route.abort("failed");
			}
			return route.fulfill({ json: [] });
		});
		await page.goto("http://stockmock-recovery.test/");
		await page.evaluate(
			({ key, order }) => sessionStorage.setItem(key, JSON.stringify(order)),
			{ key: PENDING_ORDER_KEY, order: saved },
		);
		await page.reload();
		await expectPage(page.getByLabel("访问密码")).toBeVisible();
		expect(reads).toBe(0);
		await expectPage(page.getByRole("dialog")).toHaveCount(0);
		await page.getByLabel("访问密码").fill("仅供测试");
		await page.getByRole("button", { name: "进入我的空间" }).click();
		const dialog = page.getByRole("dialog", { name: "核对上一次委托" });
		await expectPage(dialog).toContainText("暂未找到原委托");
		await expectPage(dialog).toContainText(
			"原委托已勾选同步计划，计划复盘日期 2026-11-24",
		);
		await dialog.getByRole("button", { name: "关闭", exact: true }).click();
		await page
			.getByRole("navigation")
			.getByRole("button", { name: "委托", exact: true })
			.click();
		await expectPage(dialog).toHaveCount(0);
		await page.reload();
		await expectPage(dialog).toContainText("暂未找到原委托");
		expect(reads).toBeGreaterThanOrEqual(2);
		expect(submissions).toHaveLength(0);
		await dialog.getByRole("button", { name: "按原内容重试委托" }).click();
		await expectPage(dialog.getByRole("alert")).toBeVisible();
		await page.reload();
		await expectPage(dialog).toContainText("明确拒绝");
		expect(submissions).toEqual([saved]);
		expect(retryHeaders).toEqual(["1"]);
		expect(
			await page.evaluate(
				(key) => sessionStorage.getItem(key),
				PENDING_ORDER_KEY,
			),
		).toBeNull();
	} finally {
		await browser.close();
	}
}, 25000);
