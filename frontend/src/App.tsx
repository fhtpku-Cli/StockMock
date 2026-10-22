import { Login } from "./Login";
import { Sidebar, navigation } from "./Sidebar";
import type { Page } from "./Sidebar";
import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { z } from "zod";
import {
	GearSix,
	ArrowClockwise,
	MagnifyingGlass,
} from "@phosphor-icons/react";
import { api, errorMessage, SESSION_EXPIRED } from "./api";
import { instrumentSchema, sessionSchema } from "./schema";
import type { Instrument } from "./schema";
import { ErrorNotice, Loading } from "./ui";
import { PortfolioView } from "./Portfolio";
import { Discover } from "./Discover";
import { Detail } from "./Detail";
import { Trade } from "./Trade";
import { Orders } from "./Orders";
import { Journal } from "./Journal";
import { Cash, Settings } from "./Settings";
type Overlay =
	| { readonly kind: "none" }
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
	const [overlay, setOverlay] = useState<Overlay>({ kind: "none" });
	const [journalOrder, setJournalOrder] = useState<string | null>(null);
	const [online, setOnline] = useState(navigator.onLine);
	const [sessionExpired, setSessionExpired] = useState(false);
	const [actionError, setActionError] = useState("");
	const client = useQueryClient();
	useEffect(() => {
		const up = () => {
			setOnline(true);
			void client.invalidateQueries();
		};
		const down = () => setOnline(false);
		window.addEventListener("online", up);
		window.addEventListener("offline", down);
		return () => {
			window.removeEventListener("online", up);
			window.removeEventListener("offline", down);
		};
	}, []);
	useEffect(() => {
		const expire = () => {
			setSessionExpired(true);
			setOverlay({ kind: "none" });
			setJournalOrder(null);
			setActionError("");
			void client.cancelQueries();
			client.removeQueries({ predicate: (q) => q.queryKey[0] !== "session" });
			client.setQueryData(["session"], {
				authenticated: false,
				configured: true,
			});
		};
		window.addEventListener(SESSION_EXPIRED, expire);
		return () => window.removeEventListener(SESSION_EXPIRED, expire);
	}, [client]);
	useEffect(() => {
		document.title = `${navigation.find((n) => n.id === page)?.label ?? "资产"} · 持仓练习`;
	}, [page]);
	const openSymbol = async (symbol: string) => {
		try {
			const instruments = await api(
				`instruments?q=${encodeURIComponent(symbol)}`,
				z.array(instrumentSchema),
			);
			const match = instruments.find((i) => i.symbol === symbol);
			if (match) setOverlay({ kind: "detail", instrument: match });
			else setActionError("该标的暂不在可查询范围内。");
		} catch (error) {
			if (error instanceof Error) setActionError(error.message);
			else throw error;
		}
	};
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
				setPage={setPage}
				onSettings={() => setOverlay({ kind: "settings" })}
			/>
			<main id="main" className="main-scroll">
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
							onClick={() => void client.invalidateQueries()}
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
								onClick={() => setPage("watchlist")}
							>
								<MagnifyingGlass size={18} />
								发现标的
							</button>
						)}
					</div>
					{!online && (
						<ErrorNotice message="当前离线。显示内容可能已过时，联网后请刷新；交易暂不可提交。" />
					)}
					{actionError && <ErrorNotice message={actionError} />}
					<div className="page-view">
						{page === "assets" && (
							<PortfolioView
								onExplore={() => setPage("watchlist")}
								onCash={() => setOverlay({ kind: "cash" })}
								onSymbol={(s) => void openSymbol(s)}
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
								onJournal={(id) => {
									setJournalOrder(id);
									setPage("journal");
								}}
							/>
						)}{" "}
						{page === "journal" && (
							<Journal
								orderId={journalOrder}
								onSaved={() => setJournalOrder(null)}
							/>
						)}
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
					instrument={overlay.instrument}
					onClose={close}
					onTrade={(side) =>
						setOverlay({ kind: "trade", instrument: overlay.instrument, side })
					}
				/>
			)}{" "}
			{overlay.kind === "trade" && (
				<Trade
					instrument={overlay.instrument}
					side={overlay.side}
					onClose={close}
					onSuccess={() => {
						close();
						setPage("orders");
					}}
				/>
			)}
		</div>
	);
}
