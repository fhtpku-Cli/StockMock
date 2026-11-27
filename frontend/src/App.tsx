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
import { ApiError, api, errorMessage, SESSION_EXPIRED } from "./api";
import { Cash } from "./Cash";
import { Detail } from "./Detail";
import { Discover } from "./Discover";
import { canVerifyOnBuy } from "./instrumentAdmission";
import { Journal } from "./Journal";
import { Login } from "./Login";
import { refreshWithMarketSession } from "./marketSessionQuery";
import { OrderRecovery } from "./OrderRecovery";
import { Orders } from "./Orders";
import { PortfolioView } from "./Portfolio";
import { readPendingOrder, usePendingOrder } from "./pendingOrder";
import { ReorderTrade } from "./ReorderTrade";
import { Settings } from "./Settings";
import type { Page } from "./Sidebar";
import { navigation, Sidebar } from "./Sidebar";
import type { Instrument, Order } from "./schema";
import { sessionSchema } from "./schema";
import { Trade } from "./Trade";
import { ErrorNotice, Loading, Modal } from "./ui";
import { useEnableInstrument } from "./useEnableInstrument";
import { useInstrumentNavigation } from "./useInstrumentNavigation";

type Overlay =
	| { readonly kind: "none" }
	| {
			readonly kind: "admission";
			readonly instrument: Instrument;
			readonly error?: string;
	  }
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
	const pendingOrder = usePendingOrder();
	const [recoveryOpen, setRecoveryOpen] = useState(pendingOrder);
	const session = useQuery({
		queryKey: ["session"],
		queryFn: ({ signal }) =>
			api("auth/session", sessionSchema, undefined, { signal }),
		retry: false,
	});
	const [page, setPage] = useState<Page>("assets");
	const main = useRef<HTMLElement>(null);
	const [overlay, updateOverlay] = useState<Overlay>({ kind: "none" });
	const { verify, cancel: cancelAdmission } = useEnableInstrument();
	const symbols = useInstrumentNavigation((instrument) =>
		updateOverlay({ kind: "detail", instrument }),
	);
	const setOverlay: Dispatch<SetStateAction<Overlay>> = (next) => {
		cancelAdmission();
		symbols.cancel();
		updateOverlay(next);
	};
	const [ordersEntry, setOrdersEntry] = useState<{
		readonly id: number;
		readonly filter: "all" | "active";
		readonly receipt?: Order;
		readonly locate?: string;
	}>({ id: 0, filter: "all" });
	const openOrders = (filter: "all" | "active", receipt?: Order) => {
		cancelAdmission();
		symbols.cancel();
		if (!receipt) main.current?.scrollTo({ top: 0 });
		setOrdersEntry((entry) => ({
			id: entry.id + 1,
			filter,
			...(receipt && { receipt }),
		}));
		setPage("orders");
	};
	// 只定位并高亮指定委托：不显示回执，也不清除待确认记录。
	const locateOrder = (orderId: string) => {
		cancelAdmission();
		symbols.cancel();
		main.current?.scrollTo({ top: 0 });
		setOrdersEntry((entry) => ({
			id: entry.id + 1,
			filter: "all",
			locate: orderId,
		}));
		setPage("orders");
	};
	const navigate = (next: Page) => {
		cancelAdmission();
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
		const down = () => {
			setOnline(false);
			cancelAdmission();
			updateOverlay((current) =>
				current.kind === "admission"
					? { ...current, error: "当前离线，请联网后重试。" }
					: current,
			);
		};
		window.addEventListener("online", up);
		window.addEventListener("offline", down);
		return () => {
			window.removeEventListener("online", up);
			window.removeEventListener("offline", down);
		};
	}, [client, cancelAdmission]);
	useEffect(() => {
		const expire = () => {
			setSessionExpired(true);
			cancelAdmission();
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
	}, [client, symbols.cancel, cancelAdmission]);
	useEffect(() => {
		if (session.data?.authenticated !== false) return;
		cancelAdmission();
		symbols.cancel();
		updateOverlay({ kind: "none" });
		setOrdersEntry({ id: 0, filter: "all" });
		void client.cancelQueries({
			predicate: (q) => q.queryKey[0] !== "session",
		});
		client.removeQueries({ predicate: (q) => q.queryKey[0] !== "session" });
	}, [client, session.data?.authenticated, symbols.cancel, cancelAdmission]);
	useEffect(() => {
		document.title = `${navigation.find((n) => n.id === page)?.label ?? "资产"} · 持仓练习`;
	}, [page]);
	const previousRecoveryContext = useRef({
		pending: false,
		authenticated: false,
		trading: false,
	});
	useEffect(() => {
		const authenticated = session.data?.authenticated === true;
		const trading = overlay.kind === "trade" || overlay.kind === "reorder";
		const previous = previousRecoveryContext.current;
		previousRecoveryContext.current = {
			pending: pendingOrder,
			authenticated,
			trading,
		};
		const needsAutomaticCheck =
			!previous.authenticated || !previous.pending || previous.trading;
		if (!pendingOrder || !authenticated || trading || !needsAutomaticCheck)
			return;
		try {
			const pending = readPendingOrder();
			if (
				!pending ||
				(page === "orders" &&
					ordersEntry.receipt?.request_id === pending.request_id)
			)
				return;
		} catch (error) {
			if (!(error instanceof Error)) throw error;
		}
		setRecoveryOpen(true);
	}, [
		pendingOrder,
		session.data?.authenticated,
		overlay.kind,
		page,
		ordersEntry.receipt,
	]);
	if (session.isPending)
		return (
			<div className="splash">
				<Loading />
			</div>
		);
	const recoverableSessionError =
		session.error instanceof ApiError &&
		(session.error.code === "connection" ||
			(session.error.status !== undefined && session.error.status >= 500));
	if (!session.data || (session.isError && !recoverableSessionError))
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
	const openTrade = (instrument: Instrument, side: "buy" | "sell") => {
		let needsRecovery = pendingOrder;
		try {
			needsRecovery ||= Boolean(readPendingOrder());
		} catch {
			needsRecovery = true;
		}
		if (needsRecovery) {
			setOverlay({ kind: "none" });
			setRecoveryOpen(true);
			return;
		}
		if (!navigator.onLine || instrument.market === "HK") return;
		if (instrument.tradable) {
			setOverlay({ kind: "trade", instrument, side });
		} else if (side === "buy" && canVerifyOnBuy(instrument)) {
			symbols.cancel();
			updateOverlay({ kind: "admission", instrument });
			void verify(
				instrument.symbol,
				(eligible) => openTrade(eligible, "buy"),
				(error) => updateOverlay({ kind: "admission", instrument, error }),
			);
		}
	};
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
					{session.isError && (
						<ErrorNotice
							message="暂时无法核对登录状态，正在显示上次读取的账户内容。请联网后重试。"
							retry={() => void session.refetch()}
						/>
					)}
					{!online && (
						<ErrorNotice message="当前离线。显示内容可能已过时，联网后请刷新；交易暂不可提交。" />
					)}
					{pendingOrder && (
						<button
							type="button"
							className="button"
							onClick={() => setRecoveryOpen(true)}
						>
							核对上一次委托
						</button>
					)}
					<div className="page-view">
						{page === "assets" && (
							<PortfolioView
								onOrders={() => openOrders("active")}
								onLocateOrder={locateOrder}
								onExplore={() => navigate("watchlist")}
								onCash={() => setOverlay({ kind: "cash" })}
								onSymbol={(symbol) => void symbols.open(symbol)}
								symbolError={symbols.error}
								onReviewOpen={symbols.cancel}
							/>
						)}{" "}
						{page === "watchlist" && (
							<Discover
								onBuy={(instrument) => openTrade(instrument, "buy")}
								onSelect={(instrument) =>
									setOverlay({ kind: "detail", instrument })
								}
							/>
						)}{" "}
						{page === "orders" && (
							<Orders
								key={ordersEntry.id}
								initialFilter={ordersEntry.filter}
								onReorder={(order) =>
									pendingOrder
										? setRecoveryOpen(true)
										: setOverlay({ kind: "reorder", order })
								}
								{...(ordersEntry.receipt && { receipt: ordersEntry.receipt })}
								{...(ordersEntry.locate && { locate: ordersEntry.locate })}
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
			{recoveryOpen && <OrderRecovery onClose={() => setRecoveryOpen(false)} />}
			{!recoveryOpen && overlay.kind === "settings" && (
				<Settings
					onClose={close}
					onLogout={() => {
						close();
						void session.refetch();
					}}
				/>
			)}
			{!recoveryOpen && overlay.kind === "cash" && <Cash onClose={close} />}{" "}
			{!recoveryOpen && overlay.kind === "admission" && (
				<Modal title={`${overlay.instrument.name} · 买入核验`} onClose={close}>
					{overlay.error ? (
						<>
							<ErrorNotice message={overlay.error} />
							<button
								type="button"
								className="button"
								disabled={!online}
								onClick={() => openTrade(overlay.instrument, "buy")}
							>
								重新核验
							</button>
						</>
					) : (
						<p role="status">正在核验交易条件…</p>
					)}
				</Modal>
			)}
			{!recoveryOpen && overlay.kind === "detail" && (
				<Detail
					key={overlay.instrument.symbol}
					instrument={overlay.instrument}
					onClose={close}
					onTrade={(side) => openTrade(overlay.instrument, side)}
					onLocateOrder={(orderId) => {
						close();
						locateOrder(orderId);
					}}
				/>
			)}{" "}
			{!recoveryOpen && overlay.kind === "reorder" && (
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
			{!recoveryOpen && overlay.kind === "trade" && (
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
