import { useQuery } from "@tanstack/react-query";
import { z } from "zod";
import { api, errorMessage } from "./api";
import {
	instrumentSchema,
	type Order,
	orderSchema,
	portfolioSchema,
} from "./schema";
import { Trade } from "./Trade";
import { ErrorNotice, Loading, Modal } from "./ui";
export const canReorder = (order: Order) =>
	["cancelled", "expired", "terminated"].includes(order.status);
export function ReorderTrade({
	order,
	onClose,
	onSuccess,
}: {
	readonly order: Order;
	readonly onClose: () => void;
	readonly onSuccess: () => void;
}) {
	const query = useQuery({
		queryKey: ["reorder", order.id],
		staleTime: 0,
		gcTime: 0,
		refetchOnWindowFocus: false,
		refetchOnReconnect: false,
		retry: false,
		queryFn: async () => {
			const portfolio = await api("portfolio", portfolioSchema);
			const [instruments, orders] = await Promise.all([
				api(
					`instruments?q=${encodeURIComponent(order.symbol)}`,
					z.array(instrumentSchema),
				),
				api("orders", z.array(orderSchema)),
			]);
			const current = orders.find((item) => item.id === order.id);
			if (!current || !canReorder(current))
				throw new Error(
					"原委托已不属于当前轮可再下单记录，请关闭并刷新。原记录未改动。",
				);
			const instrument = instruments.find(
				(item) => item.symbol === order.symbol,
			);
			if (!instrument) throw new Error("该证券当前无法查询，请重试。");
			return { instrument, roundId: portfolio.round_id };
		},
	});
	if (!query.isSuccess)
		return (
			<Modal title="按原参数再下单" onClose={onClose}>
				{query.isPending ? (
					<Loading />
				) : (
					<ErrorNotice
						message={errorMessage(query.error)}
						retry={() => void query.refetch()}
					/>
				)}
			</Modal>
		);
	return (
		<Trade
			instrument={query.data.instrument}
			side={order.side}
			original={order}
			draftRoundId={query.data.roundId}
			onClose={onClose}
			onSuccess={onSuccess}
		/>
	);
}
