import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createRoot } from "react-dom/client";
import { Detail } from "../src/Detail";
import "../src/styles.css";

const market = new URLSearchParams(location.search).get("market") ?? "CN";
const root = document.getElementById("root");
if (root) {
	createRoot(root).render(
		<QueryClientProvider
			client={
				new QueryClient({ defaultOptions: { queries: { retry: false } } })
			}
		>
			<Detail
				instrument={{
					symbol: "TEST",
					name: "受控成交量测试",
					market,
					currency: "CNY",
					kind: "stock",
					lot_size: 100,
					tick_size: "0.01",
					tradable: false,
					restriction: "受控界面测试，不用于交易",
				}}
				onClose={() => root.replaceChildren()}
				onTrade={() => root.replaceChildren()}
				onEnabled={() => {}}
			/>
		</QueryClientProvider>,
	);
}
