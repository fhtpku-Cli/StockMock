/** 汇率只格式化展示，原始值不进入浮点计算。 */
export function fxRate(value: string | null | undefined): string {
	if (!value || !/^\d+(?:\.\d+)?$/.test(value)) return "未知";
	const [whole, fraction = ""] = value.split(".");
	const scaled =
		BigInt(whole ?? "0") * 1000000n +
		BigInt(fraction.padEnd(6, "0").slice(0, 6));
	const rounded = scaled + (fraction[6] && fraction[6] >= "5" ? 1n : 0n);
	const digits = rounded.toString().padStart(7, "0");
	return `${digits.slice(0, -6)}.${digits.slice(-6)}`;
}
export function fxDate(value: string | null | undefined): string {
	return value && /^\d{4}-\d{2}-\d{2}(?:T|$)/.test(value)
		? `${value.slice(0, 10)} 日频参考`
		: "日频参考日期未知";
}
