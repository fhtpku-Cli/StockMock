import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createRoot } from "react-dom/client";
import { AssetHistory } from "../src/AssetHistory";
import "../src/styles.css";

// 组件测试直接渲染资产轨迹，只覆盖 12 的回落展示，不连接账户服务。
const root = document.getElementById("root");
if (root)
	createRoot(root).render(
		<QueryClientProvider
			client={
				new QueryClient({ defaultOptions: { queries: { retry: false } } })
			}
		>
			<AssetHistory />
		</QueryClientProvider>,
	);
