export function SignedValue({
	value,
	text,
}: {
	readonly value: string | null;
	readonly text: string;
}) {
	const roundedZero = value !== null && !/[1-9]/.test(text);
	const amount = roundedZero ? 0 : Number(value);
	return (
		<strong className={amount > 0 ? "gain" : amount < 0 ? "loss" : undefined}>
			{amount > 0 ? "+" : ""}
			{roundedZero ? text.replace("-", "") : text}
		</strong>
	);
}
