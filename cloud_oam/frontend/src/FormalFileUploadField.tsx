import { useEffect, useMemo, useRef, useState } from "react";

import {
  executeFormalFileUpload,
  prepareFormalFileUpload,
  type FormalFilePurpose,
  type FormalFileUploadResult,
  type FormalUploadFile,
  type PreparedFormalFileUpload,
} from "./formalFileUpload";
import { Button, showError } from "./ui";

const NONZERO_UUID = /^(?!00000000-0000-0000-0000-000000000000$)[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export type FormalFileUploadClient = Readonly<{
  prepare(file: FormalUploadFile, purpose: FormalFilePurpose): Promise<PreparedFormalFileUpload>;
  execute(prepared: PreparedFormalFileUpload): Promise<FormalFileUploadResult>;
}>;

export type AvailableFormalFile = Readonly<FormalFileUploadResult & {
  original_filename: string;
}>;

export const defaultFormalFileUploadClient: FormalFileUploadClient = Object.freeze({
  prepare: prepareFormalFileUpload,
  execute: executeFormalFileUpload,
});

type UploadStatus = "preparing" | "uploading" | "available" | "retry_required";

type UploadItem = Readonly<{
  local_id: string;
  file: FormalUploadFile;
  prepared: PreparedFormalFileUpload | null;
  result: AvailableFormalFile | null;
  status: UploadStatus;
  error: string;
}>;

function availableResult(
  prepared: PreparedFormalFileUpload,
  result: FormalFileUploadResult,
): AvailableFormalFile {
  if (
    !NONZERO_UUID.test(result.file_id)
    || result.purpose !== prepared.purpose
    || result.status !== "available"
    || result.sha256 !== prepared.sha256
    || result.size_bytes !== prepared.size_bytes
    || result.mime_type !== prepared.mime_type
  ) {
    throw new Error("文件完成结果与当前上传证据不一致，已停止业务绑定");
  }
  return Object.freeze({ ...result, original_filename: prepared.original_filename });
}

function sizeLabel(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export default function FormalFileUploadField({
  purpose,
  bindingKey,
  label,
  client = defaultFormalFileUploadClient,
  multiple = false,
  disabled = false,
  onAvailableChange,
  onBlockingChange,
}: {
  purpose: FormalFilePurpose;
  bindingKey: string;
  label: string;
  client?: FormalFileUploadClient;
  multiple?: boolean;
  disabled?: boolean;
  onAvailableChange: (files: readonly AvailableFormalFile[]) => void;
  onBlockingChange?: (blocking: boolean) => void;
}) {
  const [items, setItems] = useState<UploadItem[]>([]);
  const generation = useRef(0);
  const nextLocalId = useRef(0);
  const availableCallback = useRef(onAvailableChange);
  const blockingCallback = useRef(onBlockingChange);
  availableCallback.current = onAvailableChange;
  blockingCallback.current = onBlockingChange;

  const blocking = items.some((item) => item.status !== "available");
  const available = useMemo(
    () => items.flatMap((item) => item.result ? [item.result] : []),
    [items],
  );

  useEffect(() => {
    availableCallback.current(Object.freeze([...available]));
    blockingCallback.current?.(blocking);
  }, [available, blocking]);

  useEffect(() => {
    generation.current += 1;
    setItems([]);
    return () => { generation.current += 1; };
  }, [bindingKey, purpose, client]);

  function replace(localId: string, update: (item: UploadItem) => UploadItem): void {
    setItems((current) => current.map((item) => item.local_id === localId ? update(item) : item));
  }

  async function execute(localId: string, prepared: PreparedFormalFileUpload, selectedGeneration: number): Promise<void> {
    replace(localId, (item) => ({ ...item, prepared, status: "uploading", error: "" }));
    try {
      const result = availableResult(prepared, await client.execute(prepared));
      if (selectedGeneration !== generation.current) return;
      replace(localId, (item) => ({ ...item, prepared, result, status: "available", error: "" }));
    } catch (error) {
      if (selectedGeneration !== generation.current) return;
      replace(localId, (item) => ({ ...item, prepared, result: null, status: "retry_required", error: showError(error) }));
    }
  }

  async function addFile(file: FormalUploadFile, selectedGeneration: number): Promise<void> {
    nextLocalId.current += 1;
    const localId = `formal-upload-${nextLocalId.current}`;
    setItems((current) => [...current, { local_id: localId, file, prepared: null, result: null, status: "preparing", error: "" }]);
    try {
      const prepared = await client.prepare(file, purpose);
      if (selectedGeneration !== generation.current) return;
      await execute(localId, prepared, selectedGeneration);
    } catch (error) {
      if (selectedGeneration !== generation.current) return;
      replace(localId, (item) => ({ ...item, status: "retry_required", error: showError(error) }));
    }
  }

  async function selectFiles(files: FileList | null): Promise<void> {
    const selected = Array.from(files || []) as FormalUploadFile[];
    if (!selected.length) return;
    const selectedGeneration = generation.current;
    const bounded = multiple ? selected : selected.slice(0, 1);
    for (const file of bounded) {
      if (selectedGeneration !== generation.current) return;
      await addFile(file, selectedGeneration);
    }
  }

  return <section className="formal-file-upload" aria-label={`文件上传：${label}`}>
    <div className="form-actions">
      <label className="table-action">
        {label}
        <input
          aria-label={label}
          type="file"
          multiple={multiple}
          disabled={disabled || blocking || (!multiple && items.length > 0)}
          onChange={(event) => {
            const files = event.currentTarget.files;
            void selectFiles(files);
            event.currentTarget.value = "";
          }}
        />
      </label>
    </div>
    {!items.length && <p className="cell-subtitle">未选择文件；不接受手填 file_id，也不会调用旧 `/media`。</p>}
    {items.map((item) => <article className="formal-stocktake-observation-row" key={item.local_id} aria-label={`上传文件 ${item.file.name}`}>
      <div>
        <strong>{item.file.name}</strong>
        <span>{sizeLabel(item.file.size)} · SHA-256 {item.prepared?.sha256 || "计算中"}</span>
        <span>状态：{item.status === "preparing" ? "计算摘要" : item.status === "uploading" ? "上传并核验" : item.status === "available" ? "available（已完成严格确认）" : "失败或结果待确认"}</span>
        {item.error && <span className="negative">{item.error}</span>}
      </div>
      {item.status === "retry_required" && item.prepared && <Button tone="secondary" disabled={disabled} onClick={() => void execute(item.local_id, item.prepared as PreparedFormalFileUpload, generation.current)}>按原上传坐标重试</Button>}
      {item.status === "retry_required" && !item.prepared && <Button tone="quiet" disabled={disabled} onClick={() => setItems((current) => current.filter((row) => row.local_id !== item.local_id))}>移除无坐标文件</Button>}
      {item.status === "available" && <Button tone="quiet" disabled={disabled} onClick={() => setItems((current) => current.filter((row) => row.local_id !== item.local_id))}>移除</Button>}
    </article>)}
  </section>;
}
