import type { FillObservation } from "./schema";

export const fillObservationNotice =
	"按当时取得的行情成交，后续修订不改本笔记录。";
const observationTime = new Intl.DateTimeFormat("zh-CN", {
	timeZone: "Asia/Shanghai",
	year: "numeric",
	month: "2-digit",
	day: "2-digit",
	hour: "2-digit",
	minute: "2-digit",
	second: "2-digit",
	hour12: false,
});
export function FillObservationDetails({
	observation,
}: {
	readonly observation: FillObservation | null | undefined;
}) {
	if (!observation)
		return <p className="footnote">历史记录未保存完整行情依据。</p>;
	return (
		<>
			<dl className="summary-list">
				<div>
					<dt>成交行情来源</dt>
					<dd>{observation.source}</dd>
				</div>
				<div>
					<dt>行情区间（北京时间）</dt>
					<dd>
						{observationTime.format(new Date(observation.interval_start))} 至{" "}
						{observationTime.format(new Date(observation.market_time))}
					</dd>
				</div>
				<div>
					<dt>取得时间（北京时间）</dt>
					<dd>{observationTime.format(new Date(observation.retrieved_at))}</dd>
				</div>
				<div>
					<dt>采用价格</dt>
					<dd>
						{observation.price} {observation.currency}
					</dd>
				</div>
			</dl>
			<p className="footnote">
				{fillObservationNotice}{" "}
				供应商可能继续修订行情，因此本笔成交依据可能与后来修订的历史图表不同；不按图中高低价补成交。
			</p>
		</>
	);
}
