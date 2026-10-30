import type { ChangeDisplay } from "./marketView";

export function ChangeLine({
	value,
	compact = false,
}: {
	readonly value: ChangeDisplay;
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
			{value.change} / {value.percent}
			{!compact && <span className="muted"> · {value.base}</span>}
		</small>
	);
}
