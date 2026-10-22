import { launchBrowser } from "./browser.mjs";
import { readFile } from "node:fs/promises";
const browser = await launchBrowser();
try {
	const page = await browser.newPage();
	const svg = await readFile(
		new URL("../public/icon.svg", import.meta.url),
		"utf8",
	);
	for (const [name, size] of [
		["apple-touch-icon", 180],
		["icon-192", 192],
		["icon-512", 512],
	]) {
		await page.setViewportSize({ width: size, height: size });
		await page.setContent(
			`<style>body{margin:0;background:#276553}svg{display:block;width:80%;height:80%;padding:10%}</style>${svg}`,
		);
		await page.screenshot({
			path: new URL(`../public/${name}.png`, import.meta.url).pathname,
			omitBackground: false,
		});
	}
} finally {
	await browser.close();
}
