import assert from "node:assert/strict";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { expect, webkit } from "@playwright/test";
import { launchBrowser } from "./browser.mjs";

const url = process.env.FRONTEND_TEST_URL;
const passwordFile = process.env.FRONTEND_TEST_PASSWORD_FILE;
const output = process.env.FRONTEND_TEST_OUTPUT;
assert(url && passwordFile && output, "请指定隔离服务、测试口令文件和证据目录");
assert(
	new URL(url).hostname === "127.0.0.1" && new URL(url).port !== "8000",
	"仅允许隔离回环服务",
);
const password = (await readFile(passwordFile, "utf8")).trim();
await mkdir(output, { recursive: true });
const results = [];
for (const engine of ["chromium", "webkit"]) {
	const browser =
		engine === "chromium"
			? await launchBrowser()
			: await webkit.launch({ headless: true });
	try {
		for (const colorScheme of ["light", "dark"]) {
			const context = await browser.newContext({
				viewport: { width: 951, height: 669 },
				deviceScaleFactor: 3,
				isMobile: true,
				hasTouch: true,
				locale: "zh-CN",
				colorScheme,
				reducedMotion: "reduce",
			});
			const page = await context.newPage();
			const errors = [];
			page.on("pageerror", (error) => errors.push(error.message));
			await page.goto(url);
			await page.getByLabel("访问密码").fill(password);
			await page.getByRole("button", { name: "进入我的空间" }).click();
			await page.getByRole("button", { name: "委托", exact: true }).click();
			await expect(page.locator(".record-filters")).toBeVisible();
			for (const [width, height] of [
				[951, 669],
				[390, 844],
				[951, 669],
				[669, 951],
				[844, 390],
				[320, 568],
				[1440, 1000],
				[1920, 1080],
			]) {
				await page.setViewportSize({ width, height });
				const from = page.getByLabel("起始日期", { exact: true });
				const to = page.getByLabel("结束日期", { exact: true });
				await from.fill("2026-10-21");
				await to.fill("2026-10-27");
				await page.getByLabel("关联市场", { exact: true }).selectOption("CN");
				await expect(from).toHaveValue("2026-10-21");
				await expect(to).toHaveValue("2026-10-27");
				await expect(from).toHaveAttribute("max", "2026-10-27");
				await expect(to).toHaveAttribute("min", "2026-10-21");
				await page.locator(".record-filters").scrollIntoViewIfNeeded();
				const measured = await page
					.locator(".record-filters")
					.evaluate((container) => {
						const controls = [
							...container.querySelectorAll("input,select"),
						].map((el) => ({
							type: el.type,
							value: el.value,
							rect: el.getBoundingClientRect().toJSON(),
							label: el.parentElement.getBoundingClientRect().toJSON(),
							appearance: getComputedStyle(el).appearance,
							boxSizing: getComputedStyle(el).boxSizing,
							scrollWidth: el.scrollWidth,
							clientWidth: el.clientWidth,
						}));
						return {
							controls,
							viewport: {
								width: innerWidth,
								height: innerHeight,
								dpr: devicePixelRatio,
								coarse: matchMedia("(pointer: coarse)").matches,
							},
						};
					});
				const name = `${engine}-${colorScheme}-${results.length}-${width}x${height}`;
				await page.screenshot({ path: resolve(output, `${name}.png`) });
				results.push({ name, measured });
				await writeFile(
					resolve(output, "measurements.json"),
					JSON.stringify(results, null, 2),
				);
				for (const control of measured.controls) {
					assert(
						control.rect.left >= control.label.left - 1 &&
							control.rect.right <= control.label.right + 1,
						"日期控件不得越过父标签",
					);
					assert(
						control.rect.height >= 44 && control.rect.height <= 52,
						"触控区域必须保留且不能被原生盒模型增高",
					);
					assert(
						control.scrollWidth <= control.clientWidth + 1,
						"完整日期文字不得被挤出可视宽度",
					);
					if (control.type === "date") {
						assert.equal(
							control.appearance,
							"none",
							"日期输入不得重新进入 iOS 原生内容盒样式路径",
						);
						assert.equal(control.boxSizing, "border-box");
					}
				}
				for (const [index, first] of measured.controls.entries()) {
					for (const second of measured.controls.slice(index + 1)) {
						const horizontal =
							Math.min(first.rect.right, second.rect.right) -
							Math.max(first.rect.left, second.rect.left);
						const vertical =
							Math.min(first.rect.bottom, second.rect.bottom) -
							Math.max(first.rect.top, second.rect.top);
						assert(horizontal <= 1 || vertical <= 1, "相邻筛选控件不得相交");
					}
				}
				await page
					.getByRole("button", { name: "清除筛选", exact: true })
					.click();
				await expect(from).toHaveValue("");
				await expect(to).toHaveValue("");
				await expect(page.getByLabel("关联市场", { exact: true })).toHaveValue(
					"all",
				);
			}
			assert.deepEqual(errors, []);
			await context.close();
		}
	} finally {
		await browser.close();
	}
}
console.log(
	JSON.stringify({ passed: true, scenarios: results.length, output }),
);
