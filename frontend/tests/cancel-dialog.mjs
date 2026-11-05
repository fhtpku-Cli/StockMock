import { expect } from "@playwright/test";
import { CANCEL_CONFIRM } from "../src/Orders";

export function acceptCancelDialog(page) {
	page.once("dialog", async (dialog) => {
		expect(dialog.type()).toBe("confirm");
		expect(dialog.message()).toBe(CANCEL_CONFIRM);
		await dialog.accept();
	});
}
