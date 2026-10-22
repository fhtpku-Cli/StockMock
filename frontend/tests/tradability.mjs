import { spawn } from "node:child_process";
import { randomUUID } from "node:crypto";
import { createWriteStream } from "node:fs";
import { mkdir, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { expect, webkit } from "@playwright/test";
import { preview } from "vite";
import { launchBrowser } from "./browser.mjs";
import { uiHelpers, stopServer } from "./tradability-helpers.mjs";
import { verifyTrading } from "./tradability-trading.mjs";
const root = resolve(import.meta.dirname, "../.."),
	output = resolve(root, ".omo/evidence/tradability-ui");
await mkdir(output, { recursive: true });
const frontend = await preview({
	root: resolve(root, "frontend"),
	configFile: false,
	build: { outDir: resolve(output, "site") },
	preview: {
		host: "127.0.0.1",
		port: 5197,
		strictPort: true,
		proxy: { "/api": { target: "http://127.0.0.1:8027", changeOrigin: false } },
	},
});
const url = "http://127.0.0.1:5197",
	observations = [];
let browser, server, page;
const { capture, discover, open } = uiHelpers(() => page, output, observations);
try {
	for (const engine of ["chromium", "webkit"]) {
		const password = randomUUID(),
			log = createWriteStream(resolve(output, `${engine}-server.log`));
		server = spawn(
			resolve(root, "backend/.venv/bin/python"),
			["frontend/tests/tradability_server.py"],
			{
				cwd: root,
				env: {
					...process.env,
					TRADABILITY_TEST_PASSWORD: password,
					TRADABILITY_TEST_PORT: "8027",
				},
				stdio: ["ignore", "pipe", "pipe"],
			},
		);
		server.stdout.pipe(log);
		server.stderr.pipe(log);
		await expect
			.poll(
				async () => {
					if (server.exitCode !== null)
						throw new Error(`测试服务退出 ${server.exitCode}`);
					try {
						return (await fetch("http://127.0.0.1:8027/api/auth/session"))
							.status;
					} catch {
						return 0;
					}
				},
				{ timeout: 20000 },
			)
			.toBe(200);
		browser =
			engine === "chromium"
				? await launchBrowser()
				: await webkit.launch({ headless: true });
		const context = await browser.newContext({
			viewport: { width: 1280, height: 900 },
		});
		page = await context.newPage();
		page.setDefaultTimeout(15000);
		const errors = [],
			consoles = [];
		page.on("pageerror", (e) => errors.push(e.message));
		page.on("console", (m) => {
			if (["error", "warning"].includes(m.type())) consoles.push(m.text());
		});
		await page.goto(url);
		await expect(page).toHaveTitle("资产 · 持仓练习");
		expect(page.url()).toBe(`${url}/`);
		await page.getByLabel("访问密码").fill(password);
		await page.getByRole("button", { name: "进入我的空间" }).click();
		await expect(page.getByRole("heading", { name: "资产概览" })).toBeVisible();
		await discover();
		const catalog = await (
			await context.request.get(`${url}/api/instruments`)
		).json();
		await verifyTrading({
			page,
			engine,
			catalog,
			capture,
			open,
			discover,
			observations,
		});
		await open("00981.HK");
		await expect(
			page.getByRole("dialog").getByRole("button", { name: /启用模拟交易/ }),
		).toHaveCount(0);
		await expect(
			page
				.getByRole("dialog")
				.getByRole("button", { name: "模拟买入", exact: true }),
		).toBeDisabled();
		await capture(`${engine}-hk-observation`);
		await page.keyboard.press("Escape");
		for (const width of [1280, 390, 320]) {
			await page.setViewportSize({ width, height: 900 });
			for (const nav of ["委托", "复盘"]) {
				await page.getByRole("button", { name: nav, exact: true }).click();
				await page.getByLabel("起始日期").fill("2026-10-20");
				await page.getByLabel("结束日期").fill("2026-10-22");
				await expect(page.getByLabel("起始日期")).toHaveValue("2026-10-20");
				await expect(page.getByLabel("结束日期")).toHaveValue("2026-10-22");
				const bounds = await page.locator(".record-filters").evaluate((c) => {
					const r = c.getBoundingClientRect();
					return [...c.querySelectorAll("input,select")].map((el) => ({
						height: el.getBoundingClientRect().height,
						inside: el.getBoundingClientRect().right <= r.right + 1,
						overflow: el.scrollWidth > el.clientWidth + 1,
					}));
				});
				for (const b of bounds) {
					expect(b.inside).toBe(true);
					expect(b.overflow).toBe(false);
					expect(b.height).toBeLessThanOrEqual(52);
				}
				await capture(`${engine}-${nav}-dates-${width}`);
				await page
					.getByRole("button", { name: "清除筛选", exact: true })
					.click();
				await expect(page.getByLabel("起始日期")).toHaveValue("");
			}
		}
		expect(errors).toEqual([]);
		expect(consoles).toEqual([
			"Failed to load resource: the server responded with a status of 503 (Service Unavailable)",
			...(engine === "chromium"
				? ["Failed to load resource: net::ERR_FAILED"]
				: []),
		]);
		observations.push({
			scenario: `${engine}-控制台`,
			passed: true,
			errors,
			consoles,
			expected:
				"一次受控启用503；Chromium另记录一次受控响应丢失，除此无控制台错误",
		});
		await context.close();
		await browser.close();
		browser = undefined;
		await stopServer(server);
		log.end();
	}
	await writeFile(
		resolve(output, "results.json"),
		JSON.stringify(
			{
				passed: true,
				url,
				invocation: "cd frontend && bun tests/tradability.mjs",
				boundary:
					"隔离真实应用、真实启用核验/持久化/预览，外部身份与行情为显式受控数据；失败场景HTTP 503受控；Chrome/WebKit非真机。",
				observations,
			},
			null,
			2,
		),
	);
	console.log(
		JSON.stringify({ passed: true, scenarios: observations.length, output }),
	);
} catch (error) {
	if (page && !page.isClosed())
		await page.screenshot({ path: resolve(output, "failure.png") });
	await writeFile(
		resolve(output, "failure.json"),
		JSON.stringify({ error: String(error), observations }, null, 2),
	);
	throw error;
} finally {
	await browser?.close();
	await stopServer(server);
	await new Promise((done) => frontend.httpServer.close(done));
}
