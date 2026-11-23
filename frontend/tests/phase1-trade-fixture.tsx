import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";
import { createRoot } from "react-dom/client";
import { OrderRecovery } from "../src/OrderRecovery";
import { Orders } from "../src/Orders";
import type { Order } from "../src/schema";
import { Trade } from "../src/Trade";
import "../src/styles.css";

const client = new QueryClient({
	defaultOptions: { queries: { retry: false, staleTime: 20000 } },
});
// 组件测试用它触发计划查询的后台重新获取。
(
	window as unknown as { stockmockQueryClient: QueryClient }
).stockmockQueryClient = client;
const side =
	new URLSearchParams(location.search).get("side") === "sell" ? "sell" : "buy";
function Fixture() {
	const [screen, setScreen] = useState<"trade" | "closed" | "recovery">(
		"trade",
	);
	const [receipt, setReceipt] = useState<Order>();
	if (screen === "recovery")
		return <OrderRecovery onClose={() => setScreen("closed")} />;
	return screen === "trade" ? (
		<Trade
			instrument={{
				symbol: "AAPL",
				name: "受控证券",
				market: "US",
				currency: "USD",
				kind: "stock",
				lot_size: 1,
				tick_size: "0.01",
				tradable: true,
				restriction: null,
			}}
			side={side}
			onClose={() => setScreen("closed")}
			onSuccess={(order) => {
				setReceipt(order);
				setScreen("closed");
			}}
		/>
	) : (
		<>
			<button type="button" onClick={() => setScreen("trade")}>
				再次买入
			</button>
			<button type="button" onClick={() => setScreen("recovery")}>
				核对委托
			</button>
			{receipt && (
				<Orders receipt={receipt} initialFilter="all" onReorder={() => {}} />
			)}
		</>
	);
}
createRoot(document.getElementById("root")!).render(
	<QueryClientProvider client={client}>
		<Fixture />
	</QueryClientProvider>,
);
