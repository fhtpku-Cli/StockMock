import { execFileSync, spawn } from "node:child_process";
import { randomUUID } from "node:crypto";
import { createWriteStream } from "node:fs";
import { mkdir, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import AxeBuilder from "@axe-core/playwright";
import { expect, webkit } from "@playwright/test";
import { assetScenarios } from "./analysis-assets.mjs";
import { cashScenarios } from "./analysis-cash.mjs";
import { launchBrowser } from "./browser.mjs";
import { stopServer } from "./tradability-helpers.mjs";

const root = resolve(import.meta.dirname, "../..");
const output = resolve(
	process.env.ANALYSIS_OUTPUT ??
		resolve(root, ".omo/evidence/0.3.0/frontend/browser"),
);
const url = process.env.FRONTEND_TEST_URL ?? "http://127.0.0.1:8303";
if (new URL(url).hostname !== "127.0.0.1" || new URL(url).port === "8000")
	throw new Error("必须使用隔离本机端口");
await mkdir(output, { recursive: true });
const observations = [];
for (const engine of ["chromium", "webkit"]) {
	let browser, server, page;
	const log = createWriteStream(resolve(output, `${engine}-server.log`));
	try {
		server = spawn(
			resolve(root, "backend/.venv/bin/python"),
			["frontend/tests/analysis_server.py"],
			{
				cwd: root,
				env: {
					...process.env,
					PYTHONPATH: "backend",
					CANDIDATE_PORT: new URL(url).port,
					CANDIDATE_DB: resolve(output, `${engine}-${randomUUID()}.sqlite3`),
					CANDIDATE_DIST: resolve(root, ".omo/evidence/0.3.0/candidate-dist"),
				},
				stdio: ["ignore", "pipe", "pipe"],
			},
		);
		server.stdout.pipe(log);
		server.stderr.pipe(log);
		await expect
			.poll(
				async () => {
					try {
						return (await fetch(`${url}/api/auth/session`)).status;
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
			viewport: { width: 1440, height: 1000 },
			reducedMotion: "reduce",
		});
		page = await context.newPage();
		page.setDefaultTimeout(15000);
		const errors = [];
		page.on("pageerror", (error) => errors.push(error.message));
		const capture = async (name) => {
			const artifact = resolve(output, `${engine}-${name}.png`);
			await page.screenshot({ path: artifact });
			const overflow = await page.evaluate(() =>
				[...document.querySelectorAll(".modal[open],.main-scroll")]
					.filter(
						(element) =>
							element.getClientRects().length &&
							element.scrollWidth > element.clientWidth + 1,
					)
					.map((element) => ({
						width: element.clientWidth,
						scroll: element.scrollWidth,
					})),
			);
			expect(overflow, name).toEqual([]);
			observations.push({
				engine,
				name,
				passed: true,
				artifact,
				overflow,
				text: await page.locator("body").innerText(),
			});
		};
		const api = async (path, data) => {
			const response =
				data === undefined
					? await context.request.get(`${url}/api/${path}`)
					: await context.request.post(`${url}/api/${path}`, {
							data,
							headers: { Origin: url },
						});
			expect(response.ok(), `${path}: ${await response.text()}`).toBe(true);
			return response.json();
		};
		const control = async (mode) => {
			const response = await context.request.post(
				`${url}/_test/control?mode=${mode}`,
			);
			expect(response.ok()).toBe(true);
		};
		const download = async (button, label) => {
			const pending = page.waitForEvent("download");
			await button.click();
			const file = await pending;
			const artifact = resolve(output, `${engine}-${label}.csv`);
			await file.saveAs(artifact);
			const rows = JSON.parse(
				execFileSync(
					resolve(root, "backend/.venv/bin/python"),
					[
						"-c",
						"import csv,json,sys; print(json.dumps(list(csv.DictReader(open(sys.argv[1], encoding='utf-8-sig', newline=''))), ensure_ascii=False))",
						artifact,
					],
					{ encoding: "utf8" },
				),
			);
			observations.push({
				engine,
				name: label,
				passed: true,
				artifact,
				rows: rows.length,
			});
			return rows;
		};
		await page.goto(url);
		await page.getByLabel("访问密码").fill("analysis-local-only");
		await page.getByRole("button", { name: "进入我的空间" }).click();
		await expect(page.getByRole("heading", { name: "资产概览" })).toBeVisible();
		const harness = {
			page,
			context,
			api,
			control,
			capture,
			download,
			observations,
			engine,
			output,
		};
		await assetScenarios(harness);
		await cashScenarios(harness);
		const axe = await new AxeBuilder({ page }).analyze();
		expect(
			axe.violations.filter((item) =>
				["serious", "critical"].includes(item.impact),
			),
		).toEqual([]);
		expect(errors).toEqual([]);
		observations.push({
			engine,
			name: "complete",
			passed: true,
			pageErrors: errors,
			axeSerious: 0,
		});
	} catch (error) {
		if (page) {
			await page.screenshot({ path: resolve(output, `${engine}-failure.png`) });
			await writeFile(
				resolve(output, `${engine}-failure.txt`),
				await page.locator("body").innerText(),
			);
		}
		observations.push({
			engine,
			name: "failure",
			passed: false,
			error: String(error),
		});
		throw error;
	} finally {
		await writeFile(
			resolve(output, "observations.json"),
			JSON.stringify(observations, null, 2),
		);
		if (browser) await browser.close();
		await stopServer(server);
		log.end();
	}
}
console.log(
	JSON.stringify({ passed: true, observations: observations.length, output }),
);
