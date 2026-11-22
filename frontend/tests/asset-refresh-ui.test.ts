import { expect, test } from "bun:test";
import { existsSync } from "node:fs";
import { chromium, expect as expectPage } from "@playwright/test";

test("资产缓存、首次失败、会话恢复和双后台停滞均通过实际页面验证", async () => {
	const build = await Bun.build({
		entrypoints: [new URL("../src/main.tsx", import.meta.url).pathname],
		target: "browser",
		format: "iife",
		define: {
			"import.meta.env.DEV": "false",
			"process.env.NODE_ENV": '"production"',
		},
	});
	const script = build.outputs.find((output) => output.path.endsWith(".js"));
	if (!build.success || !script) throw new Error("应用构建失败");
	const bundle = await script.text();
	const executablePath =
		process.env.CHROME_PATH ??
		[
			"/usr/bin/google-chrome",
			"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
		].find(existsSync);
	const browser = await chromium.launch(
		executablePath ? { executablePath } : {},
	);
	const page = await browser.newPage();
	page.setDefaultTimeout(5000);
	await page.clock.install();
	let sessionMode: number | "network" | "logged-out" | "malformed" = 200;
	let assetFailure = true;
	let healthFailure = false;
	let lastRun: string | null = null;
	const amount = "1234567";
	let workerFailure = false;
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
			if (path === "/api/auth/logout") {
				sessionMode = "logged-out";
				return route.fulfill({ json: {} });
			}
			if (path === "/api/auth/login") {
				sessionMode = 200;
				return route.fulfill({ json: {} });
			}
			if (path === "/api/auth/session") {
				if (sessionMode === "network") return route.abort("failed");
				if (sessionMode === "malformed")
					return route.fulfill({ contentType: "application/json", body: "{" });
				if (typeof sessionMode === "number" && sessionMode !== 200)
					return route.fulfill({
						status: sessionMode,
						json: { detail: "会话读取失败" },
					});
				return route.fulfill({
					json: {
						authenticated: sessionMode !== "logged-out",
						configured: true,
					},
				});
			}
			if (path === "/api/portfolio") {
				if (assetFailure)
					return route.fulfill({
						status: 503,
						json: { detail: "资产读取失败" },
					});
				return route.fulfill({
					json: {
						round_id: "round-a",
						initial_capital: "1000000",
						total_assets_cny: amount,
						pnl_cny: "234567",
						valuation_status: "估值正常",
						cash: [
							{
								currency: "CNY",
								balance: amount,
								available: amount,
								reserved: "0",
							},
						],
						positions: [],
						worker: { last_run: null, status: "starting" },
						warnings: [],
					},
				});
			}
			if (path === "/api/health") {
				if (healthFailure)
					return route.fulfill({
						status: 503,
						json: { detail: "后台读取失败" },
					});
				return route.fulfill({
					json: {
						last_run: lastRun,
						status: workerFailure
							? "交易处理暂不可用，下轮重试"
							: lastRun
								? "running"
								: "starting",
						plan_worker: {
							last_run: lastRun,
							status: lastRun ? "running" : "starting",
						},
					},
				});
			}
			return route.fulfill({ json: [] });
		});
		await page.goto("https://stockmock-assets.test/");
		const refresh = page.getByRole("button", { name: "刷新数据", exact: true });
		const assets = page.getByText("1,234,567.00", { exact: false }).first();
		await expectPage(
			page.getByRole("alert").filter({ hasText: "资产读取失败" }),
		).toBeVisible();
		await expectPage(assets).toHaveCount(0);
		assetFailure = false;
		await page
			.getByRole("alert")
			.filter({ hasText: "资产读取失败" })
			.getByRole("button", { name: /重试/ })
			.click();
		await expectPage(assets).toBeVisible();
		const health = page.getByRole("region", { name: "后台处理状态" });
		await expectPage(health).toContainText("交易处理 · 准备中");
		await expectPage(health).toContainText("计划观察 · 准备中");
		healthFailure = true;
		assetFailure = true;
		await refresh.click();
		const cachedError = page
			.getByRole("alert")
			.filter({ hasText: "资产刷新失败" });
		await expectPage(cachedError).toContainText("最近成功读取的数据（北京时间");
		await expectPage(assets).toBeVisible();
		await expectPage(health).toContainText("后台状态暂时无法刷新");
		await page.clock.fastForward(181000);
		await expectPage(health).toContainText("交易处理 · 可能延迟");
		await expectPage(health).toContainText("计划观察 · 可能延迟");
		await expectPage(health).not.toContainText("正常");
		healthFailure = false;
		lastRun = new Date(await page.evaluate(() => Date.now())).toISOString();
		assetFailure = false;
		await cachedError.getByRole("button", { name: /重试/ }).click();
		await refresh.click();
		await expectPage(cachedError).toHaveCount(0);
		await expectPage(health).toContainText("交易处理 · 正常");
		await expectPage(health).toContainText("计划观察 · 正常");
		await expectPage(health).not.toContainText("可能延迟");
		lastRun = new Date(
			(await page.evaluate(() => Date.now())) - 181000,
		).toISOString();
		await refresh.click();
		await expectPage(health).toContainText("交易处理 · 可能延迟");
		await expectPage(health).toContainText("计划观察 · 可能延迟");
		await expectPage(health).not.toContainText("正常");
		workerFailure = true;
		await refresh.click();
		await expectPage(health).toContainText("交易处理 · 暂不可用，下轮重试");
		await expectPage(health).not.toContainText("交易处理 · 交易处理");
		console.log(
			"PASS 两后台 starting 缓存刷新失败三分钟计时、恢复、旧 running 延迟及失败文案",
		);
		await page.clock.resume();
		for (const mode of ["network", 503] as const) {
			sessionMode = mode;
			await refresh.click();
			await expectPage(
				page.getByRole("alert").filter({ hasText: "暂时无法核对登录状态" }),
			).toBeVisible();
			await expectPage(assets).toBeVisible();
			sessionMode = 200;
			await refresh.click();
			await expectPage(
				page.getByRole("alert").filter({ hasText: "暂时无法核对登录状态" }),
			).toHaveCount(0);
		}
		console.log(
			"PASS 首次资产失败重试、缓存金额和时间、网络与503会话保留及恢复",
		);
		for (const mode of [400, "malformed"] as const) {
			sessionMode = mode;
			await refresh.click();
			await expectPage(assets).toHaveCount(0);
			await expectPage(page.getByRole("alert")).toBeVisible();
			sessionMode = 200;
			await page
				.getByRole("alert")
				.getByRole("button", { name: /重试/ })
				.click();
			await expectPage(assets).toBeVisible();
		}
		console.log("PASS 会话400及损坏JSON阻止继续展示账户，确认恢复后重新显示");
		for (const mode of [401, 403, "logged-out"] as const) {
			sessionMode = mode;
			if (mode === "logged-out") {
				await page.clock.fastForward(21000);
				await page.evaluate(() =>
					window.dispatchEvent(new Event("visibilitychange")),
				);
			} else {
				await refresh.click();
			}
			await expectPage(page.getByLabel("访问密码")).toBeVisible();
			await expectPage(assets).toHaveCount(0);
			assetFailure = true;
			await page.getByLabel("访问密码").fill("测试");
			await page.getByRole("button", { name: "进入我的空间" }).click();
			await expectPage(
				page.getByRole("alert").filter({ hasText: "资产读取失败" }),
			).toBeVisible();
			await expectPage(assets).toHaveCount(0);
			assetFailure = false;
			await refresh.click();
			await expectPage(assets).toBeVisible();
		}
		console.log(
			"PASS 会话401、403、authenticated:false清缓存及重新登录资产失败隔离",
		);
		await page.getByRole("button", { name: "账户设置" }).click();
		await page.getByRole("button", { name: "退出登录", exact: true }).click();
		await expectPage(page.getByLabel("访问密码")).toBeVisible();
		assetFailure = true;
		await page.getByLabel("访问密码").fill("测试");
		await page.getByRole("button", { name: "进入我的空间" }).click();
		await expectPage(
			page.getByRole("alert").filter({ hasText: "资产读取失败" }),
		).toBeVisible();
		await expectPage(assets).toHaveCount(0);
		assetFailure = false;
		await refresh.click();
		await expectPage(assets).toBeVisible();
		console.log("PASS 实际退出登录及再次登录失败时旧金额不可见");
		expect(
			await page
				.getByRole("heading", { name: "资产概览", exact: true })
				.count(),
		).toBe(1);
	} catch (error) {
		console.log(await page.locator("body").textContent());
		throw error;
	} finally {
		await browser.close();
	}
}, 60000);
