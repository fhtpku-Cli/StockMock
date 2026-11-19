import { expect } from "@playwright/test";
import { CANCEL_CONFIRM } from "../src/Orders";

export async function acceptCancelDialog(page) {
	const dialog = page.getByRole("dialog", { name: "确认申请撤单" });
	await expect(dialog.locator(".notice")).toHaveText(CANCEL_CONFIRM);
	await dialog.getByRole("button", { name: "确认申请撤单" }).click();
}
