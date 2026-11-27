import { CaretDown, CaretUp } from "@phosphor-icons/react";
import { queryOptions, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api, errorMessage, fullTime } from "./api";
import {
	episodeSummary,
	eventDetailTexts,
	eventKindLabels,
	eventMainText,
	eventTimeText,
	episodesKey,
	holdingText,
	OUTSIDE_EVENTS_LABEL,
	peakText,
	reconcileText,
} from "./episodeView";
import { episodesSchema, type Episode, type EpisodeEvent } from "./schema";
import { ErrorNotice } from "./ui";

export function episodesQuery(symbol: string) {
	return queryOptions({
		queryKey: episodesKey(symbol),
		queryFn: ({ signal }) =>
			api(
				`positions/${encodeURIComponent(symbol)}/episodes`,
				episodesSchema,
				undefined,
				{ signal },
			),
	});
}

function EventRow({
	event,
	onLocateOrder,
}: {
	readonly event: EpisodeEvent;
	readonly onLocateOrder: (orderId: string) => void;
}) {
	const orderId = event.order_id;
	const details = eventDetailTexts(event);
	return (
		<li className="episode-event" data-event-kind={event.kind}>
			<span className="episode-event-kind">{eventKindLabels[event.kind]}</span>
			<div className="episode-event-body">
				<small className="episode-event-time">{eventTimeText(event)}</small>
				<p className="episode-event-main">{eventMainText(event)}</p>
				{details.length > 0 && (
					<p className="episode-event-details">{details.join(" · ")}</p>
				)}
				{event.note !== null && event.kind !== "anomaly" && (
					<p className="episode-event-note">{event.note}</p>
				)}
				{orderId !== null && (
					<button
						type="button"
						className="text-button"
						data-order-id={orderId}
						onClick={() => onLocateOrder(orderId)}
					>
						在委托记录中查看
					</button>
				)}
			</div>
		</li>
	);
}

function EpisodeRow({
	episode,
	defaultOpen,
	onLocateOrder,
}: {
	readonly episode: Episode;
	readonly defaultOpen: boolean;
	readonly onLocateOrder: (orderId: string) => void;
}) {
	const [open, setOpen] = useState(defaultOpen);
	const closed = episode.status === "closed";
	return (
		<li className="episode" data-episode-index={episode.index}>
			<button
				type="button"
				className="episode-summary"
				aria-expanded={open}
				onClick={() => setOpen(!open)}
			>
				<span>{episodeSummary(episode)}</span>
				{open ? <CaretUp size={16} /> : <CaretDown size={16} />}
			</button>
			{open && (
				<div className="episode-detail">
					<dl className="episode-facts">
						<div>
							<dt>状态</dt>
							<dd>{closed ? "已清仓" : "进行中"}</dd>
						</div>
						<div>
							<dt>首买时间</dt>
							<dd>{fullTime(episode.opened_at)}（北京时间）</dd>
						</div>
						{episode.closed_at !== null && (
							<div>
								<dt>清仓时间</dt>
								<dd>{fullTime(episode.closed_at)}（北京时间）</dd>
							</div>
						)}
						<div>
							<dt>持有天数</dt>
							<dd>{holdingText(episode)}</dd>
						</div>
						<div>
							<dt>买卖次数</dt>
							<dd>
								买 {episode.buy_count} 次 · 卖 {episode.sell_count} 次
							</dd>
						</div>
						<div>
							<dt>段内最大持仓</dt>
							<dd>{peakText(episode)}</dd>
						</div>
					</dl>
					{episode.boundary_note !== null && (
						<p className="notice">{episode.boundary_note}</p>
					)}
					<ol className="episode-events">
						{episode.events.map((event, index) => (
							<EventRow
								key={`${event.kind}-${event.at}-${index}`}
								event={event}
								onLocateOrder={onLocateOrder}
							/>
						))}
					</ol>
				</div>
			)}
		</li>
	);
}

/** 证券详情页「建仓到清仓」：只读当前轮次，失败不影响详情页其他部分。 */
export function Episodes({
	symbol,
	onLocateOrder,
}: {
	readonly symbol: string;
	readonly onLocateOrder: (orderId: string) => void;
}) {
	const query = useQuery(episodesQuery(symbol));
	const data = query.data;
	if (!data) {
		if (query.isPending) return null;
		return (
			<ErrorNotice
				message="建仓到清仓回看暂不可用，请稍后重试。"
				retry={() => void query.refetch()}
			/>
		);
	}
	// 本轮无成交与调整时不占位。
	if (data.episodes.length === 0 && data.outside_events.length === 0)
		return null;
	const mismatch = reconcileText(data);
	return (
		<section className="episodes" aria-label="建仓到清仓">
			<div className="section-heading">
				<h3>建仓到清仓</h3>
				<span className="muted">本轮分段回看</span>
			</div>
			<p className="footnote">
				本轮持仓从 0 变正再归零为一段，最近一段在上；金额按原币显示。
			</p>
			{mismatch !== null && <p className="notice">{mismatch}</p>}
			{query.isError && (
				<ErrorNotice
					message={`建仓到清仓回看刷新失败，当前显示最近成功读取的内容。${errorMessage(query.error)}`}
					retry={() => void query.refetch()}
				/>
			)}
			<ul className="episode-list">
				{data.episodes.map((episode, index) => (
					<EpisodeRow
						key={episode.index}
						episode={episode}
						defaultOpen={index === 0}
						onLocateOrder={onLocateOrder}
					/>
				))}
			</ul>
			{data.outside_events.length > 0 && (
				<div className="episode-outside">
					<h4>{OUTSIDE_EVENTS_LABEL}</h4>
					<p className="footnote">未落入任何持仓段，仅作记录展示。</p>
					<ul className="episode-events">
						{data.outside_events.map((event, index) => (
							<EventRow
								key={`${event.kind}-${event.at}-${index}`}
								event={event}
								onLocateOrder={onLocateOrder}
							/>
						))}
					</ul>
				</div>
			)}
		</section>
	);
}
