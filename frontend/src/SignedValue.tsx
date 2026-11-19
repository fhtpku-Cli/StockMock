export function SignedValue({
	value,
	text,
}: {
	readonly value: string | null;
	readonly text: string;
}) {
	const amount = Number(value);
	return (
		<strong className={amount > 0 ? "gain" : amount < 0 ? "loss" : undefined}>
			{amount > 0 ? "+" : ""}
			{text}
		</strong>
	);
}
