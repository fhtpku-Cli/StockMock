import type { z } from "zod";
import { time } from "./api";
import type { candidateIntervalSchema } from "./schema";
export function CandidateInterval({
	interval,
	activeAt,
	terminal = false,
}: {
	readonly interval?: z.infer<typeof candidateIntervalSchema> | undefined;
	readonly activeAt?: string | undefined;
	readonly terminal?: boolean;
}) {
	return (
		<div className="notice">
			{activeAt && <p>委托生效时间：{time(activeAt)}（北京时间）</p>}
			<p>
				{interval?.status === "available" && interval.start && interval.end
					? `首个候选区间为 ${time(interval.start)} 至 ${time(interval.end)}（北京时间）。`
					: interval?.status === "none"
						? "本单截止前没有完整候选区间，不会新增成交机会；尚未终态时等待后台确认。"
						: "首个候选区间未知，暂无法确认交易日历。"}
			</p>
			{interval?.reason && interval.status === "unknown" && (
				<p>{interval.reason}</p>
			)}
			<p className="footnote">
				{terminal ? (
					"以上仅解释原委托的候选资格。本单已结束，以实际结果为准，不会再成交。"
				) : (
					<>
						候选区间不代表成交时间。有效行情到达并满足价格条件才可能成交；后台约每分钟检查，受网络与来源影响。撤单确认前，资金或持仓继续占用。
					</>
				)}
			</p>
		</div>
	);
}
