import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FileJson2, FileText, UploadCloud, X } from "lucide-react";
import { DragEvent, useEffect, useMemo, useRef, useState } from "react";
import { apiFetch, emitToast } from "../lib/api";
import { parseCookieFiles, parseCookieText, type CookieImportItem } from "../lib/cookies";
import type { FleetInstance } from "../types";
import { Modal } from "./Modal";

interface ImportSummary {
  total: number;
  imported: number;
  failed: number;
  refreshed: number;
  refreshFailed: number;
}

export function CookieImportModal({
  open,
  onClose,
  fixedInstanceId,
  fixedInstanceName,
  onImported
}: {
  open: boolean;
  onClose: () => void;
  fixedInstanceId?: string;
  fixedInstanceName?: string;
  onImported?: () => void;
}) {
  const queryClient = useQueryClient();
  const inputRef = useRef<HTMLInputElement>(null);
  const [mode, setMode] = useState<"paste" | "files">("paste");
  const [targetId, setTargetId] = useState(fixedInstanceId || "");
  const [text, setText] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [fileItems, setFileItems] = useState<CookieImportItem[]>([]);
  const [fileError, setFileError] = useState("");
  const [result, setResult] = useState<ImportSummary | null>(null);
  const instances = useQuery({
    queryKey: ["instances"],
    queryFn: () => apiFetch<{ instances: FleetInstance[] }>("/instances"),
    enabled: open && !fixedInstanceId
  });

  useEffect(() => {
    if (!open) return;
    setTargetId(fixedInstanceId || "");
    setText("");
    setFiles([]);
    setFileItems([]);
    setFileError("");
    setResult(null);
    setMode("paste");
  }, [open, fixedInstanceId]);

  const pasteParse = useMemo(() => {
    try {
      return { items: parseCookieText(text), error: "" };
    } catch (error) {
      return { items: [] as CookieImportItem[], error: error instanceof Error ? error.message : "Cookie 解析失败" };
    }
  }, [text]);
  const selectedItems = mode === "paste" ? pasteParse.items : fileItems;
  const parseError = mode === "paste" ? pasteParse.error : fileError;

  async function setSelectedFiles(nextFiles: File[]) {
    setFiles(nextFiles);
    setFileError("");
    try {
      setFileItems(await parseCookieFiles(nextFiles));
    } catch (error) {
      setFileItems([]);
      setFileError(error instanceof Error ? error.message : "Cookie 文件解析失败");
    }
  }

  function dropFiles(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    void setSelectedFiles(Array.from(event.dataTransfer.files));
  }

  const importCookies = useMutation({
    mutationFn: async () => {
      if (!targetId) throw new Error("请选择目标实例");
      if (!selectedItems.length) throw new Error(parseError || "请粘贴或选择 Cookie 文件");
      const body = selectedItems.length === 1 ? selectedItems[0] : { items: selectedItems };
      return apiFetch<Record<string, unknown>>(`/instances/${targetId}/refresh-profiles/import`, {
        method: "POST",
        body: JSON.stringify(body)
      });
    },
    onSuccess: async (payload) => {
      const isBatch = selectedItems.length > 1;
      const refreshError = String(payload.refresh_error || "").trim();
      const summary: ImportSummary = isBatch ? {
        total: selectedItems.length,
        imported: Number(payload.imported_count || 0),
        failed: Number(payload.failed_count || 0),
        refreshed: Number(payload.refreshed_count || 0),
        refreshFailed: Number(payload.refresh_failed_count || 0)
      } : {
        total: 1,
        imported: 1,
        failed: 0,
        refreshed: refreshError ? 0 : 1,
        refreshFailed: refreshError ? 1 : 0
      };
      setResult(summary);
      emitToast(summary.failed || summary.refreshFailed ? "Cookie 已导入，部分账号需要处理" : "Cookie 账号导入完成", summary.failed || summary.refreshFailed ? "info" : "success");
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["accounts"] }),
        queryClient.invalidateQueries({ queryKey: ["instance-accounts", targetId] }),
        queryClient.invalidateQueries({ queryKey: ["dashboard"] }),
        queryClient.invalidateQueries({ queryKey: ["instance", targetId] })
      ]);
      onImported?.();
    },
    onError: (error) => emitToast(error.message, "error")
  });

  const footer = result ? (
    <button className="primary-btn" onClick={onClose}>完成</button>
  ) : (
    <><button className="secondary-btn" onClick={onClose}>取消</button><button className="primary-btn" disabled={!targetId || !selectedItems.length || Boolean(parseError) || importCookies.isPending} onClick={() => importCookies.mutate()}>{importCookies.isPending ? "正在导入" : `导入 ${selectedItems.length || ""} 个账号`}</button></>
  );

  return <Modal open={open} title="导入 Cookie 账号" onClose={onClose} footer={footer}>
    <div className="cookie-import">
      <label>目标实例
        {fixedInstanceId ? <strong className="fixed-target">{fixedInstanceName || fixedInstanceId}</strong> : <select value={targetId} onChange={(event) => setTargetId(event.target.value)}><option value="">选择实例</option>{instances.data?.instances.filter((item) => item.enabled).map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select>}
      </label>
      {!result && <>
        <div className="segmented-control" aria-label="Cookie 输入方式">
          <button className={mode === "paste" ? "active" : ""} onClick={() => setMode("paste")}><FileText size={15} />粘贴</button>
          <button className={mode === "files" ? "active" : ""} onClick={() => setMode("files")}><FileJson2 size={15} />文件</button>
        </div>
        {mode === "paste" ? <textarea className="cookie-paste" rows={10} value={text} onChange={(event) => setText(event.target.value)} placeholder={'Cookie: k1=v1; k2=v2\n\n或粘贴浏览器导出的 JSON'} /> : <>
          <div className="cookie-dropzone" role="button" tabIndex={0} onDragOver={(event) => event.preventDefault()} onDrop={dropFiles} onClick={() => inputRef.current?.click()} onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") inputRef.current?.click(); }}>
            <UploadCloud size={24} /><strong>选择或拖放 Cookie 文件</strong><span>TXT / JSON，可多选</span>
          </div>
          <input ref={inputRef} className="visually-hidden" type="file" accept=".txt,.json,text/plain,application/json" multiple onChange={(event) => void setSelectedFiles(Array.from(event.target.files || []))} />
          <div className="cookie-files">{files.map((file, index) => <div key={`${file.name}-${index}`}><FileJson2 size={15} /><span>{file.name}</span><small>{Math.max(1, Math.round(file.size / 1024))} KB</small><button className="icon-btn" title="移除文件" onClick={() => void setSelectedFiles(files.filter((_, itemIndex) => itemIndex !== index))}><X size={14} /></button></div>)}</div>
        </>}
        <div className={`parse-status${parseError ? " parse-error" : ""}`}>{parseError || `已解析 ${selectedItems.length} 个 Cookie 账号`}</div>
      </>}
      {result && <div className="import-result"><div><span>解析</span><strong>{result.total}</strong></div><div><span>导入成功</span><strong>{result.imported}</strong></div><div><span>导入失败</span><strong className={result.failed ? "text-danger" : ""}>{result.failed}</strong></div><div><span>刷新成功</span><strong>{result.refreshed}</strong></div><div><span>刷新失败</span><strong className={result.refreshFailed ? "text-danger" : ""}>{result.refreshFailed}</strong></div></div>}
    </div>
  </Modal>;
}
