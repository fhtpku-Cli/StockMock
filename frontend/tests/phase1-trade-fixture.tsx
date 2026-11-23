import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";
import { createRoot } from "react-dom/client";
import { OrderRecovery } from "../src/OrderRecovery";
import { Trade } from "../src/Trade";
import "../src/styles.css";

const client = new QueryClient({
	defaultOptions: { queries: { retry: false, staleTime: 20000 } },
});
function Fixture() {
	const [screen, setScreen] = useState<"trade" | "closed" | "recovery">(
		"trade",
	);
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
			side="buy"
			onClose={() => setScreen("closed")}
			onSuccess={() => setScreen("closed")}
		/>
	) : (
		<>
			<button type="button" onClick={() => setScreen("trade")}>
				再次买入
			</button>
			<button type="button" onClick={() => setScreen("recovery")}>
				核对委托
			</button>
		</>
	);
}
createRoot(document.getElementById("root")!).render(
	<QueryClientProvider client={client}>
		<Fixture />
	</QueryClientProvider>,
);
