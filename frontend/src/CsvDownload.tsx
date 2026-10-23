import { DownloadSimple } from "@phosphor-icons/react";
import { useMutation } from "@tanstack/react-query";
import { downloadCsv, errorMessage } from "./api";
import { ErrorNotice } from "./ui";
export function CsvDownload({
	path,
	name,
	children,
}: {
	readonly path: string;
	readonly name: string;
	readonly children: string;
}) {
	const download = useMutation({
		mutationFn: () => downloadCsv(path),
		onSuccess: (blob) => {
			const url = URL.createObjectURL(blob);
			const anchor = document.createElement("a");
			anchor.href = url;
			anchor.download = name;
			document.body.append(anchor);
			anchor.click();
			anchor.remove();
			window.setTimeout(() => URL.revokeObjectURL(url), 1000);
		},
	});
	return (
		<div className="csv-download">
			<button
				type="button"
				className="text-button"
				disabled={download.isPending}
				onClick={() => download.mutate()}
			>
				<DownloadSimple size={18} />
				{download.isPending ? "正在导出…" : children}
			</button>
			{download.isError && (
				<ErrorNotice
					message={errorMessage(download.error)}
					retry={() => download.mutate()}
				/>
			)}
		</div>
	);
}
