import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, errorMessage } from "./api";
import { JournalNotes } from "./JournalNotes";
import { Reviews } from "./Reviews";
import { acknowledgmentSchema } from "./schema";
import { ErrorNotice, Panel } from "./ui";
import { useOnline } from "./useOnline";
export function Journal() {
	const [text, setText] = useState("");
	const online = useOnline();
	const client = useQueryClient();
	const save = useMutation({
		mutationFn: () =>
			api("journal", acknowledgmentSchema, {
				text,
			}),
		onSuccess: async () => {
			setText("");
			await client.invalidateQueries({ queryKey: ["journal"] });
		},
	});
	return (
		<div className="stack">
			<Reviews />
			<details className="panel journal-secondary">
				<summary>普通笔记与记录</summary>
				<Panel title="写普通笔记">
					<form
						onSubmit={(event) => {
							event.preventDefault();
							if (online && text.trim()) save.mutate();
						}}
					>
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
								这条笔记独立保存，不计入正式复盘。
							</span>
							<button
								type="submit"
								className="button primary"
								disabled={!online || save.isPending || !text.trim()}
							>
								{save.isPending ? "保存中…" : "保存记录"}
							</button>
						</div>
						{save.isError && <ErrorNotice message={errorMessage(save.error)} />}
						{save.isSuccess && (
							<p role="status" className="positive">
								普通笔记已保存。
							</p>
						)}
					</form>
				</Panel>
				<JournalNotes />
			</details>
		</div>
	);
}
