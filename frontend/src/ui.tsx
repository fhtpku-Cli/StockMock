import {
	ArrowClockwise,
	ChartLine,
	WarningCircle,
	X,
} from "@phosphor-icons/react";
import type { ReactNode } from "react";
import { useEffect, useRef } from "react";
export function Panel({
	title,
	action,
	children,
	className = "",
}: {
	readonly title: string;
	readonly action?: ReactNode;
	readonly children: ReactNode;
	readonly className?: string;
}) {
	return (
		<section className={`panel ${className}`}>
			<div className="section-heading">
				<h2>{title}</h2>
				{action}
			</div>
			{children}
		</section>
	);
}
export function Empty({
	title,
	children,
	action,
}: {
	readonly title: string;
	readonly children: ReactNode;
	readonly action?: ReactNode;
}) {
	return (
		<div className="empty">
			<span className="empty-icon">
				<ChartLine size={28} />
			</span>
			<h3>{title}</h3>
			<p>{children}</p>
			{action}
		</div>
	);
}
export function ErrorNotice({
	message,
	retry,
}: {
	readonly message: string;
	readonly retry?: () => void;
}) {
	return (
		<div className="error" role="alert">
			<WarningCircle size={20} />
			<span>{message}</span>
			{retry && (
				<button type="button" className="text-button" onClick={retry}>
					<ArrowClockwise />
					重试
				</button>
			)}
		</div>
	);
}
export function Loading() {
	return (
		<div className="loading" role="status">
			<span className="spinner" />
			正在同步账户…
		</div>
	);
}
export function Badge({
	children,
	tone = "neutral",
}: {
	readonly children: ReactNode;
	readonly tone?: "neutral" | "good" | "warn";
}) {
	return <span className={`badge ${tone}`}>{children}</span>;
}
export function Modal({
	title,
	children,
	onClose,
	wide = false,
}: {
	readonly title: string;
	readonly children: ReactNode;
	readonly onClose: () => void;
	readonly wide?: boolean;
}) {
	const ref = useRef<HTMLDialogElement>(null);
	useEffect(() => {
		const dialog = ref.current;
		dialog?.showModal();
		const viewport = window.visualViewport;
		const resize = () => {
			if (dialog && viewport) {
				dialog.style.setProperty("--visible-height", `${viewport.height}px`);
				dialog.style.setProperty("--visible-top", `${viewport.offsetTop}px`);
			}
		};
		resize();
		viewport?.addEventListener("resize", resize);
		viewport?.addEventListener("scroll", resize);
		return () => {
			viewport?.removeEventListener("resize", resize);
			viewport?.removeEventListener("scroll", resize);
			dialog?.close();
		};
	}, []);
	return (
		<dialog
			aria-label={title}
			ref={ref}
			className={wide ? "modal wide" : "modal"}
			onCancel={onClose}
			onKeyDown={(event) => {
				if (event.key !== "Tab") return;
				const dialog = event.currentTarget;
				const controls = Array.from(
					dialog.querySelectorAll<HTMLElement>(
						'button:not(:disabled), a[href], input:not(:disabled), select:not(:disabled), textarea:not(:disabled), summary, [tabindex]:not([tabindex="-1"])',
					),
				).filter(
					(element) =>
						element.getClientRects().length > 0 &&
						element.closest("dialog") === dialog,
				);
				if (controls.length === 0) return;
				event.preventDefault();
				const current = controls.findIndex(
					(element) => element === document.activeElement,
				);
				const next = event.shiftKey
					? current <= 0
						? controls.length - 1
						: current - 1
					: (current + 1) % controls.length;
				controls[next]?.focus();
			}}
		>
			<div className="modal-heading">
				<h2>{title}</h2>
				<button
					type="button"
					className="icon-button"
					aria-label="关闭"
					onClick={onClose}
				>
					<X size={22} />
				</button>
			</div>
			<div className="modal-body">{children}</div>
		</dialog>
	);
}
