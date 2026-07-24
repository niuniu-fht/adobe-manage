import { FileJson2, FileText, UploadCloud, X } from "lucide-react";
import { DragEvent, useEffect, useMemo, useRef, useState } from "react";
import {
  parseCookieFiles,
  parseCookieText,
  type CookieImportItem
} from "../lib/cookies";

export function CookieSourceInput({
  onChange
}: {
  onChange: (items: CookieImportItem[], error: string) => void;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const onChangeRef = useRef(onChange);
  const [mode, setMode] = useState<"paste" | "files">("paste");
  const [text, setText] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [fileItems, setFileItems] = useState<CookieImportItem[]>([]);
  const [fileError, setFileError] = useState("");

  useEffect(() => {
    onChangeRef.current = onChange;
  }, [onChange]);

  const pasteParse = useMemo(() => {
    try {
      return { items: parseCookieText(text), error: "" };
    } catch (error) {
      return {
        items: [] as CookieImportItem[],
        error: error instanceof Error ? error.message : "Cookie 解析失败"
      };
    }
  }, [text]);
  const selectedItems = mode === "paste" ? pasteParse.items : fileItems;
  const parseError = mode === "paste" ? pasteParse.error : fileError;

  useEffect(() => {
    onChangeRef.current(selectedItems, parseError);
  }, [selectedItems, parseError]);

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

  return <>
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
  </>;
}
