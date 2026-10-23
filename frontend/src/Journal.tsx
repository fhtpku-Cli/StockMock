import { Notebook } from "@phosphor-icons/react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, errorMessage } from "./api";
import { CashStatement } from "./CashStatement";
import { JournalNotes } from "./JournalNotes";
import { Reviews } from "./Reviews";
import { acknowledgmentSchema } from "./schema";
import { ErrorNotice, Panel } from "./ui";
import { useOnline } from "./useOnline";
export function Journal({
	orderId,
	onSaved,
}: {
	readonly orderId: string | null;
	readonly onSaved: () => void;
}) {
	const [text, setText] = useState("");
	const online = useOnline();
	const client = useQueryClient();
	const save = useMutation({
		mutationFn: () =>
			api("journal", acknowledgmentSchema, {
				text,
				...(orderId ? { order_id: orderId } : {}),
			}),
		onSuccess: async () => {
			setText("");
			onSaved();
			await client.invalidateQueries({ queryKey: ["journal"] });
		},
	});
	return (
		<div className="stack">
			<div className="journal-intro">
				<Notebook size={36} weight="duotone" />
				<div>
					<h2>把每一次判断，留给未来的自己。</h2>
					<p>普通笔记记录想法，正式结论记录一次完整复盘。</p>
				</div>
			</div>
			<Reviews />
			<div className="dashboard-grid">
				<div className="stack">
					<Panel title="写下这一刻的判断">
						<form
							onSubmit={(event) => {
								event.preventDefault();
								if (online && text.trim()) save.mutate();
							}}
						>
							{orderId && <p className="notice">关联委托 {orderId}</p>}
							<label className="field">
								普通笔记
								<textarea
									aria-label="复盘内容"
									className="journal-input"
									required
									maxLength={5000}
									value={text}
									onChange={(event) => setText(event.target.value)}
									disabled={save.isPending}
								/>
							</label>
							<div className="form-footer">
								<span className="footnote">
									留给自己，不公开分享；普通笔记不标记复盘完成。
								</span>
								<button
									type="submit"
									className="button primary"
									disabled={!online || save.isPending || !text.trim()}
								>
									{save.isPending ? "保存中…" : "保存记录"}
								</button>
							</div>
							{save.isError && (
								<ErrorNotice message={errorMessage(save.error)} />
							)}
							{save.isSuccess && (
								<p role="status" className="positive">
									普通笔记已保存。
								</p>
							)}
						</form>
					</Panel>
					<JournalNotes />
				</div>
				<CashStatement />
			</div>
		</div>
	);
}
