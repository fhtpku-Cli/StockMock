import { price } from "./api";
import type { ChangeDisplay } from "./marketView";

export function ChangeLine({
	value,
	currency,
	compact = false,
}: {
	readonly value: ChangeDisplay;
	readonly currency: string;
	readonly compact?: boolean;
}) {
	if (value.kind === "message")
		return (
			<small className="change-line muted" data-change="message">
				{value.label}：{value.text}
			</small>
		);
	return (
		<small className={`change-line ${value.direction}`} data-change="value">
			{value.label} {value.direction === "up" ? "+" : ""}
			{price(value.change, currency)} / {value.percent}
			{!compact && <span className="muted"> · {value.base}</span>}
		</small>
	);
}
