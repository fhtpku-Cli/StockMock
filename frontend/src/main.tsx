import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import React from "react";
import ReactDOM from "react-dom/client";
import { App } from "./App";
import "./styles.css";

if (import.meta.env.DEV) {
	void import("react-grab");
	void import("react-scan").then(({ scan }) => scan({ enabled: false }));
}
const client = new QueryClient({
	defaultOptions: {
		queries: { retry: false, staleTime: 20000, refetchOnWindowFocus: true },
	},
});
const root = document.getElementById("root");
if (root)
	ReactDOM.createRoot(root).render(
		<React.StrictMode>
			<QueryClientProvider client={client}>
				<App />
			</QueryClientProvider>
		</React.StrictMode>,
	);
