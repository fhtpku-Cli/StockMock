import { spawn } from "node:child_process";
import { randomUUID } from "node:crypto";
import { createWriteStream } from "node:fs";
import { mkdir, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { devices, expect, webkit } from "@playwright/test";
import { launchBrowser } from "./browser.mjs";

const root = resolve(import.meta.dirname, "../..");
const output = resolve(
	root,
	process.env.LAYOUT_TEST_OUTPUT ?? ".omo/evidence/mobile-layout-20261023",
);
const phase = process.env.LAYOUT_TEST_PHASE ?? "after";
const url = "http://127.0.0.1:8017";
const password = randomUUID();
await mkdir(output, { recursive: true });
const serverLog = createWriteStream(resolve(output, `server-${phase}.log`));
const server = spawn(
	resolve(root, "backend/.venv/bin/python"),
	["frontend/tests/record_layout_server.py"],
	{
		cwd: root,
		env: {
			...process.env,
			LAYOUT_TEST_PASSWORD: password,
			LAYOUT_TEST_DIST: resolve(
				root,
				process.env.LAYOUT_TEST_DIST ?? resolve(output, "site"),
			),
		},
		stdio: ["ignore", "pipe", "pipe"],
	},
);
server.stdout.pipe(serverLog);
server.stderr.pipe(serverLog);
const results = [];
let browser;
let activePage;
const measure = (page) =>
	page.locator(".record-filters").evaluate((container) => {
		const bounds = (el) => {
			const r = el.getBoundingClientRect();
			return {
				x: r.x,
				y: r.y,
				width: r.width,
				height: r.height,
				right: r.right,
				bottom: r.bottom,
			};
		};
		return {
			container: bounds(container),
			labels: [...container.querySelectorAll("label")].map(bounds),
			controls: [...container.querySelectorAll("input,select")].map((el) => ({
				...bounds(el),
				scrollWidth: el.scrollWidth,
				clientWidth: el.clientWidth,
				fontSize: getComputedStyle(el).fontSize,
			})),
			clear: bounds(container.querySelector("button")),
			scrollWidth: document.querySelector(".main-scroll").scrollWidth,
			clientWidth: document.querySelector(".main-scroll").clientWidth,
		};
	});
try {
	await expect
		.poll(
			async () => {
				if (server.exitCode !== null)
					throw new Error(`隔离服务退出：${server.exitCode}`);
				try {
					return (await fetch(`${url}/api/auth/session`)).status;
				} catch {
					return 0;
				}
			},
			{ timeout: 15000 },
		)
		.toBe(200);
	for (const engine of ["chromium", "webkit"]) {
		browser =
			engine === "chromium"
				? await launchBrowser()
				: await webkit.launch({ headless: true });
		const sizes =
			phase === "before"
				? [[390, 844]]
				: [
						[320, 568],
						[375, 667],
						[390, 844],
						[430, 932],
						[844, 390],
						[1280, 900],
					];
		const errors = [];
		for (const [width, height] of sizes) {
			const context = await browser.newContext({
				...devices["iPhone 13"],
				viewport: { width, height },
				locale: "zh-CN",
			});
			const page = await context.newPage();
			activePage = page;
			page.on("pageerror", (error) => errors.push(error.message));
			page.on("console", (message) => {
				if (["error", "warning"].includes(message.type()))
					errors.push(message.text());
			});
			await page.goto(url);
			await expect(page).toHaveTitle("资产 · 持仓练习");
			await page.getByLabel("访问密码").fill(password);
			await page.getByRole("button", { name: "进入我的空间" }).click();
			await expect(
				page.getByRole("heading", { name: "资产概览" }),
			).toBeVisible();
			const note = await context.request.post(`${url}/api/journal`, {
				data: { text: "日期筛选布局受控测试" },
			});
			expect(note.status()).toBe(200);
			for (const [surface, nav] of [
				["orders", "委托"],
				["journal", "复盘"],
			]) {
				await page.getByRole("button", { name: nav, exact: true }).click();
				await expect(page).toHaveTitle(`${nav} · 持仓练习`);
				await expect(page.locator(".record-filters")).toBeVisible();
				await expect(page.locator("vite-error-overlay")).toHaveCount(0);
				await page.locator(".main-scroll").evaluate((el) => {
					el.scrollTop = 0;
				});
				const initial = await measure(page);
				const prefix = `${phase}-${engine}-${surface}-${width}x${height}`;
				await page.screenshot({ path: resolve(output, `${prefix}.png`) });
				if (phase === "before") {
					expect(initial.container.height).toBeGreaterThan(450);
					expect(initial.labels[0].height).toBeGreaterThanOrEqual(140);
					expect(initial.labels[0].x - initial.container.x).toBeGreaterThan(
						100,
					);
				}
				if (phase !== "before") {
					expect(initial.scrollWidth).toBeLessThanOrEqual(initial.clientWidth);
					for (const control of initial.controls) {
						expect(control.x).toBeGreaterThanOrEqual(initial.container.x - 1);
						expect(control.right).toBeLessThanOrEqual(
							initial.container.right + 1,
						);
						expect(control.height).toBeGreaterThanOrEqual(44);
						expect(control.height).toBeLessThanOrEqual(52);
						expect(control.scrollWidth).toBeLessThanOrEqual(
							control.clientWidth + 1,
						);
					}
					for (const label of initial.labels)
						expect(label.height).toBeLessThan(90);
					if (width < 701 || height < 501) {
						for (const label of initial.labels) {
							expect(Math.abs(label.x - initial.container.x)).toBeLessThan(1);
							expect(
								Math.abs(label.width - initial.container.width),
							).toBeLessThan(1);
						}
					}
					await page.getByLabel("起始日期").fill("2026-10-21");
					await page.getByLabel("结束日期").fill("2026-10-22");
					await expect(page.getByLabel("起始日期")).toHaveValue("2026-10-21");
					await expect(page.getByLabel("结束日期")).toHaveValue("2026-10-22");
					const selected = await measure(page);
					for (const control of selected.controls) {
						expect(control.right).toBeLessThanOrEqual(
							selected.container.right + 1,
						);
						expect(control.scrollWidth).toBeLessThanOrEqual(
							control.clientWidth + 1,
						);
					}
					await page.locator(".record-filters").scrollIntoViewIfNeeded();
					await page.screenshot({
						path: resolve(output, `${prefix}-selected.png`),
					});
					await expect(page.getByLabel("起始日期")).toHaveAttribute(
						"max",
						"2026-10-22",
					);
					await expect(page.getByLabel("结束日期")).toHaveAttribute(
						"min",
						"2026-10-21",
					);
					await page.getByLabel("关联市场").selectOption("CN");
					await expect(page.getByLabel("关联市场")).toHaveValue("CN");
					if (surface === "journal")
						await expect(page.locator(".journal-list article")).toHaveCount(0);
					await page
						.getByRole("button", { name: "清除筛选", exact: true })
						.click();
					await expect(page.getByLabel("起始日期")).toHaveValue("");
					await expect(page.getByLabel("结束日期")).toHaveValue("");
					await expect(page.getByLabel("关联市场")).toHaveValue("all");
					if (surface === "journal") {
						await expect(page.locator(".journal-list article")).not.toHaveCount(
							0,
						);
						await page.getByLabel("起始日期").fill("2026-10-23");
						await expect(page.locator(".journal-list article")).toHaveCount(0);
						await page
							.getByRole("button", { name: "清除筛选", exact: true })
							.click();
					}
					await page.locator(".main-scroll").evaluate((el) => {
						el.scrollTop = el.scrollHeight;
					});
					const footer = await page.locator(".page-footer").boundingBox();
					const sidebar = await page.locator(".sidebar").boundingBox();
					if (width < 701 || height < 501)
						expect(footer.y + footer.height).toBeLessThanOrEqual(sidebar.y + 1);
					await page.screenshot({
						path: resolve(output, `${prefix}-scrolled.png`),
					});
					await page.getByRole("button", { name: "资产", exact: true }).click();
					await expect(
						page.getByRole("heading", { name: "资产概览" }),
					).toBeVisible();
				}
				results.push({
					engine,
					surface,
					width,
					height,
					initial,
					interactionPassed: phase !== "before",
				});
			}
			await context.close();
		}
		expect(errors).toEqual([]);
		results.push({ engine, errors, version: browser.version() });
		await browser.close();
		browser = undefined;
	}
	await writeFile(
		resolve(output, `${phase}.json`),
		JSON.stringify(
			{
				phase,
				source:
					"受控行情、独立临时 SQLite、随机测试口令、Playwright 实际浏览器，非 iPhone 真机",
				url,
				results,
			},
			null,
			2,
		),
	);
	console.log(
		JSON.stringify({
			phase,
			scenarios: results.filter((x) => x.surface).length,
			passed: true,
			output,
		}),
	);
} catch (error) {
	if (activePage && !activePage.isClosed()) {
		await activePage.screenshot({
			path: resolve(output, `${phase}-failure.png`),
		});
		await writeFile(
			resolve(output, `${phase}-failure-dom.json`),
			JSON.stringify(
				await activePage.evaluate(() => ({
					width: innerWidth,
					height: innerHeight,
					viewport: {
						width: visualViewport.width,
						height: visualViewport.height,
						scale: visualViewport.scale,
					},
					nav: [...document.querySelectorAll(".nav-item")].map((el) => ({
						text: el.textContent,
						rect: el.getBoundingClientRect().toJSON(),
					})),
				})),
				null,
				2,
			),
		);
	}
	await writeFile(
		resolve(output, `${phase}-failure.json`),
		JSON.stringify({ error: String(error), results }, null, 2),
	);
	throw error;
} finally {
	await browser?.close();
	server.kill("SIGTERM");
	await new Promise((done) =>
		server.exitCode !== null ? done() : server.once("exit", done),
	);
	serverLog.end();
}
