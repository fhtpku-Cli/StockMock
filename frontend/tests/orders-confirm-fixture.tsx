import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createRoot } from "react-dom/client";
import { Orders } from "../src/Orders";

const root = document.getElementById("root");
if (root) {
	createRoot(root).render(
		<QueryClientProvider
			client={
				new QueryClient({ defaultOptions: { queries: { retry: false } } })
			}
		>
			<Orders initialFilter="all" onJournal={() => {}} onReorder={() => {}} />
		</QueryClientProvider>,
	);
}
