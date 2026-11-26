import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";
import { createRoot } from "react-dom/client";
import { Orders } from "../src/Orders";
import { RecentEndings } from "../src/RecentEndings";
import { Trade } from "../src/Trade";
import "../src/styles.css";

const params = new URLSearchParams(location.search);
const tradeMode = params.get("trade") === "1";
const initialLocate = params.get("locate");
const client = new QueryClient({
	defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
});
// 组件测试用它触发查询的后台重新获取。
(
	window as unknown as { stockmockQueryClient: QueryClient }
).stockmockQueryClient = client;
/** 资产页摘要块与委托页（或下单窗）同页渲染：定位与失效重取都能直接观察。 */
function Fixture() {
	const [locating, setLocating] = useState<string | null>(initialLocate);
	return (
		<QueryClientProvider client={client}>
			<p id="assets-rest">资产页其余部分</p>
			<RecentEndings roundId="r1" onLocate={setLocating} />
			{tradeMode ? (
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
					onClose={() => {}}
					onSuccess={() => {}}
				/>
			) : (
				<Orders
					initialFilter="all"
					onReorder={() => {}}
					{...(locating && { locate: locating })}
				/>
			)}
		</QueryClientProvider>
	);
}
const root = document.getElementById("root");
if (root) createRoot(root).render(<Fixture />);
