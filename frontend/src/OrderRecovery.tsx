import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useEffectEvent, useRef, useState } from "react";
import { api, errorMessage, fullTime, price } from "./api";
import { OrderRecord } from "./OrderRecord";
import {
	clearPendingOrder,
	findOrderReceipt,
	isFinalOrderRejection,
	readPendingOrderRecord,
	type Submission,
} from "./pendingOrder";
import { type Order, orderSchema } from "./schema";
import { ErrorNotice, Modal } from "./ui";

type Recovery =
	| { readonly kind: "checking" }
	| { readonly kind: "missing" }
	| {
			readonly kind: "accepted";
			readonly order: Order;
			readonly retried: boolean;
	  }
	| { readonly kind: "rejected" }
	| { readonly kind: "error" };

export function OrderRecovery({ onClose }: { readonly onClose: () => void }) {
	const client = useQueryClient();
	const [pending, setPending] = useState<Submission | null>(null);
	const [currency, setCurrency] = useState<string | undefined>();
	const [result, setResult] = useState<Recovery>({ kind: "checking" });
	const [error, setError] = useState("");
	const [busy, setBusy] = useState(false);
	const lock = useRef(false);
	const mounted = useRef(true);
	const run = async (retry: boolean) => {
		if (lock.current) return;
		lock.current = true;
		setBusy(true);
		setError("");
		let saved: Submission | null = null;
		try {
			const record = readPendingOrderRecord();
			saved = record?.submission ?? null;
			setPending(saved);
			setCurrency(record?.currency);
			if (!saved) {
				onClose();
				return;
			}
			const order = retry
				? await api("orders", orderSchema, saved, {
						headers: { "X-StockMock-Retry": "1" },
					})
				: await findOrderReceipt(saved.request_id);
			if (!mounted.current) return;
			setResult(
				order
					? { kind: "accepted", order, retried: retry }
					: { kind: "missing" },
			);
			if (order) {
				void client.invalidateQueries({ queryKey: ["portfolio"] });
				void client.invalidateQueries({ queryKey: ["orders"] });
			}
		} catch (failure) {
			if (!mounted.current) return;
			setResult({ kind: "error" });
			setError(errorMessage(failure));
			if (isFinalOrderRejection(failure, true)) {
				try {
					if (saved) clearPendingOrder(saved.request_id);
					setPending(null);
					setResult({ kind: "rejected" });
				} catch (storageError) {
					setError(errorMessage(storageError));
				}
			}
		} finally {
			lock.current = false;
			if (mounted.current) setBusy(false);
		}
	};
	const checkOnOpen = useEffectEvent(() => {
		void run(false);
	});
	useEffect(() => {
		mounted.current = true;
		checkOnOpen();
		return () => {
			mounted.current = false;
		};
	}, []);
	const acknowledge = () => {
		try {
			if (pending && result.kind === "accepted")
				clearPendingOrder(pending.request_id);
			onClose();
		} catch (failure) {
			setError(errorMessage(failure));
		}
	};
	let message: string;
	switch (result.kind) {
		case "checking":
			message = "正在只读核对原委托…";
			break;
		case "missing":
			message =
				"暂未找到原委托。原请求可能仍在途中，请稍后核对，或明确重试原委托。";
			break;
		case "accepted":
			message =
				(result.retried ? "原委托已受理。" : "已确认原委托曾被受理。") +
				"以下显示原委托的最新状态，归档后的记录属于原练习轮次。";
			break;
		case "rejected":
			message = "原委托已明确拒绝。可以关闭后重新建立委托。";
			break;
		case "error":
			message = "核对暂未完成。请恢复连接或登录后继续核对。";
			break;
	}
	const displayCurrency =
		result.kind === "accepted" ? result.order.currency : currency;
	const displayPrice =
		result.kind === "accepted"
			? (result.order.limit_price ?? result.order.price_bound)
			: (pending?.limit_price ?? null);
	return (
		<Modal title="核对上一次委托" closeDisabled={busy} onClose={acknowledge}>
			<p className="notice" role="status">
				{message}
			</p>
			{pending && (
				<p>
					{pending.symbol} · {pending.side === "buy" ? "买入" : "卖出"}{" "}
					{pending.quantity} 股 / 份 ·{" "}
					{pending.order_type === "market"
						? "市价委托"
						: `限价 ${
								displayCurrency
									? price(displayPrice, displayCurrency)
									: `${pending.limit_price ?? "—"}（币种待核对）`
							}`}
					。原有效期至 {fullTime(pending.expected_expires_at)}（北京时间）。
				</p>
			)}
			{result.kind === "accepted" && <OrderRecord order={result.order} />}
			{error && <ErrorNotice message={error} />}
			{result.kind !== "accepted" && result.kind !== "rejected" && (
				<div className="button-row">
					<button
						type="button"
						className="button"
						disabled={busy}
						onClick={() => void run(false)}
					>
						只读核对结果
					</button>
					{pending && (
						<button
							type="button"
							className="button primary"
							disabled={busy}
							onClick={() => void run(true)}
						>
							按原内容重试委托
						</button>
					)}
				</div>
			)}
			{(result.kind === "accepted" || result.kind === "rejected") && (
				<button type="button" className="button primary" onClick={acknowledge}>
					知道了
				</button>
			)}
			{pending && result.kind !== "accepted" && (
				<p className="footnote">
					可以关闭后查看账户。建立下一笔委托前，需要先完成这笔核对。
				</p>
			)}
		</Modal>
	);
}
