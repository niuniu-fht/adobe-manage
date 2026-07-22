import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { AlertCircle, ExternalLink, RefreshCw, Search } from "lucide-react";
import { useMemo, useState } from "react";
import { Modal } from "../components/Modal";
import { apiFetch, formatDuration, formatTime } from "../lib/api";
import type { FleetInstance, LogItem } from "../types";

interface LogsResponse {
  status: string;
  items: LogItem[];
  errors: { instance_name: string; detail: string }[];
  next_cursor?: string | null;
}

export function LogsPage({ fixedInstanceId }: { fixedInstanceId?: string }) {
  const [instanceId, setInstanceId] = useState(fixedInstanceId || "");
  const [promptInput, setPromptInput] = useState("");
  const [prompt, setPrompt] = useState("");
  const [errorsOnly, setErrorsOnly] = useState(false);
  const [detail, setDetail] = useState<LogItem | null>(null);
  const [errorDetail, setErrorDetail] = useState<unknown>(null);
  const instances = useQuery({
    queryKey: ["instances"],
    queryFn: () => apiFetch<{ instances: FleetInstance[] }>("/instances")
  });
  const logs = useInfiniteQuery({
    queryKey: ["logs", instanceId, prompt, errorsOnly],
    initialPageParam: "",
    queryFn: ({ pageParam }) => {
      const params = new URLSearchParams({ limit: "100", prompt, errors_only: String(errorsOnly) });
      if (instanceId) params.set("instance_ids", instanceId);
      if (pageParam) params.set("cursor", pageParam);
      return apiFetch<LogsResponse>(`/logs?${params}`);
    },
    getNextPageParam: (lastPage) => lastPage.next_cursor || undefined,
    refetchInterval: 30000
  });
  const rows = useMemo(() => logs.data?.pages.flatMap((page) => page.items) || [], [logs.data]);
  const remoteErrors = logs.data?.pages[0]?.errors || [];

  async function openError(item: LogItem) {
    setDetail(item);
    setErrorDetail(null);
    if (!item.error_code) return;
    try {
      setErrorDetail(await apiFetch(`/instances/${item.instance_id}/logs/errors/${encodeURIComponent(item.error_code)}`));
    } catch (error) {
      setErrorDetail({ detail: error instanceof Error ? error.message : "详情读取失败" });
    }
  }

  function mediaUrl(item: LogItem) {
    if (!item.preview_url) return "";
    try {
      const pathname = new URL(item.preview_url, window.location.origin).pathname;
      const filename = pathname.split("/").pop();
      return filename ? `/api/instances/${item.instance_id}/generated/${encodeURIComponent(filename)}` : item.preview_url;
    } catch {
      return item.preview_url;
    }
  }

  return (
    <div className="page-stack">
      {!fixedInstanceId && <section className="page-toolbar"><div><strong>跨实例日志</strong><span>{rows.length} 条已加载</span></div><button className="secondary-btn" onClick={() => logs.refetch()}><RefreshCw size={16} />刷新</button></section>}
      <section className="filter-bar">
        {!fixedInstanceId && <select value={instanceId} onChange={(event) => setInstanceId(event.target.value)}><option value="">全部实例</option>{instances.data?.instances.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select>}
        <label className="search-box"><Search size={16} /><input value={promptInput} onChange={(event) => setPromptInput(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") setPrompt(promptInput); }} placeholder="搜索 Prompt" /></label>
        <button className="secondary-btn" onClick={() => setPrompt(promptInput)}>搜索</button>
        <label className="check-row"><input type="checkbox" checked={errorsOnly} onChange={(event) => setErrorsOnly(event.target.checked)} />仅错误</label>
      </section>
      {remoteErrors.length > 0 && <div className="warning-banner">{remoteErrors.map((item) => `${item.instance_name}: ${item.detail}`).join(" · ")}</div>}
      <section className="table-section">
        <div className="table-scroll">
          <table>
            <thead><tr><th>时间</th><th>实例</th><th>请求</th><th>模型</th><th>Prompt</th><th>状态</th><th>耗时</th><th>结果</th></tr></thead>
            <tbody>
              {rows.map((item, index) => (
                <tr key={`${item.instance_id}-${item.id}-${index}`}>
                  <td>{formatTime(item.ts)}</td>
                  <td><strong>{item.instance_name}</strong></td>
                  <td><code>{item.method || "POST"} {item.path || item.operation}</code></td>
                  <td>{item.model || "-"}</td>
                  <td><button className="text-button log-prompt" onClick={() => setDetail(item)}>{item.prompt_preview || item.prompt || "-"}</button></td>
                  <td><button className={`http-status status-code-${Math.floor((item.status_code || 0) / 100)}xx`} onClick={() => openError(item)}>{item.status_code || item.task_status || "-"}</button></td>
                  <td>{formatDuration(item.duration_sec)}</td>
                  <td>{item.preview_url ? <a className="icon-btn" href={mediaUrl(item)} target="_blank" rel="noreferrer" title="打开结果"><ExternalLink size={16} /></a> : item.error ? <button className="icon-btn danger-icon" onClick={() => openError(item)} title="错误详情"><AlertCircle size={16} /></button> : "-"}</td>
                </tr>
              ))}
              {!logs.isLoading && rows.length === 0 && <tr><td colSpan={8} className="empty-row">没有匹配日志</td></tr>}
            </tbody>
          </table>
        </div>
        {logs.hasNextPage && <div className="load-more"><button className="secondary-btn" onClick={() => logs.fetchNextPage()} disabled={logs.isFetchingNextPage}>{logs.isFetchingNextPage ? "加载中..." : "加载更多"}</button></div>}
      </section>
      <Modal open={Boolean(detail)} title={detail?.error ? "错误详情" : "请求详情"} onClose={() => { setDetail(null); setErrorDetail(null); }} wide>
        {detail && <div className="detail-grid"><span>实例</span><strong>{detail.instance_name}</strong><span>时间</span><strong>{formatTime(detail.ts)}</strong><span>模型</span><strong>{detail.model || "-"}</strong><span>状态</span><strong>{detail.status_code || detail.task_status}</strong></div>}
        <h3 className="detail-title">Prompt</h3><pre className="detail-pre">{detail?.prompt || detail?.prompt_preview || "-"}</pre>
        {Boolean(detail?.error || errorDetail) && <><h3 className="detail-title">错误</h3><pre className="detail-pre error-pre">{JSON.stringify(errorDetail || { error: detail?.error }, null, 2)}</pre></>}
      </Modal>
    </div>
  );
}
