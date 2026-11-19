import {
	ArrowClockwise,
	GearSix,
	MagnifyingGlass,
} from "@phosphor-icons/react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
	type Dispatch,
	type SetStateAction,
	useEffect,
	useRef,
	useState,
} from "react";
import { api, errorMessage, SESSION_EXPIRED } from "./api";
import { Cash } from "./Cash";
import { Detail } from "./Detail";
import { Discover } from "./Discover";
import { Journal } from "./Journal";
import { Login } from "./Login";
import { refreshWithMarketSession } from "./marketSessionQuery";
import { Orders } from "./Orders";
import { PortfolioView } from "./Portfolio";
import { ReorderTrade } from "./ReorderTrade";
import { Settings } from "./Settings";
import type { Page } from "./Sidebar";
import { navigation, Sidebar } from "./Sidebar";
import type { Instrument, Order } from "./schema";
import { sessionSchema } from "./schema";
import { Trade } from "./Trade";
import { ErrorNotice, Loading } from "./ui";
import { useInstrumentNavigation } from "./useInstrumentNavigation";

type Overlay =
	| { readonly kind: "none" }
	| { readonly kind: "reorder"; readonly order: Order }
	| { readonly kind: "settings" }
	| { readonly kind: "cash" }
	| { readonly kind: "detail"; readonly instrument: Instrument }
	| {
			readonly kind: "trade";
			readonly instrument: Instrument;
			readonly side: "buy" | "sell";
	  };
export function App() {
	const session = useQuery({
		queryKey: ["session"],
		queryFn: () => api("auth/session", sessionSchema),
		retry: false,
	});
	const [page, setPage] = useState<Page>("assets");
	const main = useRef<HTMLElement>(null);
	const [overlay, updateOverlay] = useState<Overlay>({ kind: "none" });
	const symbols = useInstrumentNavigation((instrument) =>
		updateOverlay({ kind: "detail", instrument }),
	);
	const setOverlay: Dispatch<SetStateAction<Overlay>> = (next) => {
		symbols.cancel();
		updateOverlay(next);
	};
	const [ordersEntry, setOrdersEntry] = useState<{
		readonly id: number;
		readonly filter: "all" | "active";
		readonly receipt?: Order;
	}>({ id: 0, filter: "all" });
	const openOrders = (filter: "all" | "active", receipt?: Order) => {
		symbols.cancel();
		if (!receipt) main.current?.scrollTo({ top: 0 });
		setOrdersEntry((entry) => ({
			id: entry.id + 1,
			filter,
			...(receipt && { receipt }),
		}));
		setPage("orders");
	};
	const navigate = (next: Page) => {
		symbols.cancel();
		main.current?.scrollTo({ top: 0 });
		if (next === "orders") openOrders("all");
		else setPage(next);
	};
	const [online, setOnline] = useState(navigator.onLine);
	const [sessionExpired, setSessionExpired] = useState(false);
	const client = useQueryClient();
	useEffect(() => {
		const up = () => {
			setOnline(true);
			void refreshWithMarketSession(client);
		};
		const down = () => setOnline(false);
		window.addEventListener("online", up);
		window.addEventListener("offline", down);
		return () => {
			window.removeEventListener("online", up);
			window.removeEventListener("offline", down);
		};
	}, [client]);
	useEffect(() => {
		const expire = () => {
			setSessionExpired(true);
			symbols.cancel();
			updateOverlay({ kind: "none" });
			setOrdersEntry({ id: 0, filter: "all" });
			void client.cancelQueries();
			client.removeQueries({ predicate: (q) => q.queryKey[0] !== "session" });
			client.setQueryData(["session"], {
				authenticated: false,
				configured: true,
			});
		};
		window.addEventListener(SESSION_EXPIRED, expire);
		return () => window.removeEventListener(SESSION_EXPIRED, expire);
	}, [client, symbols.cancel]);
	useEffect(() => {
		document.title = `${navigation.find((n) => n.id === page)?.label ?? "资产"} · 持仓练习`;
	}, [page]);
	if (session.isPending)
		return (
			<div className="splash">
				<Loading />
			</div>
		);
	if (session.isError)
		return (
			<div className="splash">
				<ErrorNotice
					message={errorMessage(session.error)}
					retry={() => void session.refetch()}
				/>
			</div>
		);
	if (!session.data.authenticated)
		return (
			<Login
				configured={session.data.configured}
				expired={sessionExpired}
				onSuccess={() => {
					setSessionExpired(false);
					void session.refetch();
				}}
			/>
		);
	const current = navigation.find((n) => n.id === page) ?? navigation[0];
	const close = () => setOverlay({ kind: "none" });
	return (
		<div className="app-shell">
			<a className="skip-link" href="#main">
				跳到主要内容
			</a>
			<Sidebar
				page={page}
				setPage={navigate}
				onSettings={() => setOverlay({ kind: "settings" })}
			/>
			<main id="main" className="main-scroll" ref={main}>
				<header className="topbar">
					<div className="breadcrumb">
						我的空间 <span>/</span> {current.label}
					</div>
					<div className="top-actions">
						<span className="session-label">
							<span />
							私人练习中
						</span>
						<button
							type="button"
							className="icon-button"
							aria-label="刷新数据"
							onClick={() => void refreshWithMarketSession(client)}
						>
							<ArrowClockwise size={20} />
						</button>
						<button
							type="button"
							className="icon-button mobile-settings"
							aria-label="账户设置"
							onClick={() => setOverlay({ kind: "settings" })}
						>
							<GearSix size={20} />
						</button>
					</div>
				</header>
				<div className="page-content">
					<div className="page-heading">
						<div>
							<h1>{page === "assets" ? "资产概览" : current.label}</h1>
							<p>{current.subtitle}</p>
						</div>
						{page === "assets" && (
							<button
								type="button"
								className="button"
								onClick={() => navigate("watchlist")}
							>
								<MagnifyingGlass size={18} />
								发现标的
							</button>
						)}
					</div>
					{!online && (
						<ErrorNotice message="当前离线。显示内容可能已过时，联网后请刷新；交易暂不可提交。" />
					)}
					<div className="page-view">
						{page === "assets" && (
							<PortfolioView
								onOrders={() => openOrders("active")}
								onExplore={() => navigate("watchlist")}
								onCash={() => setOverlay({ kind: "cash" })}
								onSymbol={(symbol) => void symbols.open(symbol)}
								symbolError={symbols.error}
								onReviewOpen={symbols.cancel}
							/>
						)}{" "}
						{page === "watchlist" && (
							<Discover
								onSelect={(instrument) =>
									setOverlay({ kind: "detail", instrument })
								}
							/>
						)}{" "}
						{page === "orders" && (
							<Orders
								key={ordersEntry.id}
								initialFilter={ordersEntry.filter}
								onReorder={(order) => setOverlay({ kind: "reorder", order })}
								{...(ordersEntry.receipt && { receipt: ordersEntry.receipt })}
							/>
						)}{" "}
						{page === "journal" && <Journal />}
					</div>
					<footer className="page-footer">
						<span>持仓练习 · 不涉及真实资金</span>
						<span>行情有延迟，决策留记录</span>
					</footer>
				</div>
			</main>
			{overlay.kind === "settings" && (
				<Settings
					onClose={close}
					onLogout={() => {
						close();
						void session.refetch();
					}}
				/>
			)}
			{overlay.kind === "cash" && <Cash onClose={close} />}{" "}
			{overlay.kind === "detail" && (
				<Detail
					key={overlay.instrument.symbol}
					instrument={overlay.instrument}
					onClose={close}
					onEnabled={(instrument) =>
						setOverlay((current) =>
							current.kind === "detail" &&
							current.instrument.symbol === instrument.symbol
								? { kind: "detail", instrument }
								: current,
						)
					}
					onTrade={(side) =>
						setOverlay({ kind: "trade", instrument: overlay.instrument, side })
					}
				/>
			)}{" "}
			{overlay.kind === "reorder" && (
				<ReorderTrade
					key={overlay.order.id}
					order={overlay.order}
					onClose={close}
					onSuccess={(order) => {
						close();
						openOrders("all", order);
					}}
				/>
			)}
			{overlay.kind === "trade" && (
				<Trade
					instrument={overlay.instrument}
					side={overlay.side}
					onClose={() =>
						setOverlay({ kind: "detail", instrument: overlay.instrument })
					}
					onSuccess={(order) => {
						close();
						openOrders("all", order);
					}}
				/>
			)}
		</div>
	);
}
