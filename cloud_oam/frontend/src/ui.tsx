import type { ButtonHTMLAttributes, PropsWithChildren, ReactNode } from "react";
import { LoaderCircle, X } from "lucide-react";

export function Button({
  tone = "primary",
  icon,
  children,
  className = "",
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  tone?: "primary" | "secondary" | "danger" | "quiet";
  icon?: ReactNode;
}) {
  return (
    <button className={`button button-${tone} ${className}`} {...props}>
      {icon}
      {children && <span>{children}</span>}
    </button>
  );
}

export function IconButton({ label, children, ...props }: PropsWithChildren<{ label: string } & ButtonHTMLAttributes<HTMLButtonElement>>) {
  return (
    <button className="icon-button" title={label} aria-label={label} {...props}>
      {children}
    </button>
  );
}

export function Modal({ title, onClose, children, wide = false }: PropsWithChildren<{ title: string; onClose: () => void; wide?: boolean }>) {
  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <section className={`modal ${wide ? "modal-wide" : ""}`} role="dialog" aria-modal="true" aria-label={title}>
        <header className="modal-header">
          <h2>{title}</h2>
          <IconButton label="关闭" onClick={onClose}><X size={20} /></IconButton>
        </header>
        <div className="modal-body">{children}</div>
      </section>
    </div>
  );
}

export function Loading({ label = "正在读取" }: { label?: string }) {
  return <div className="loading"><LoaderCircle size={22} className="spin" /><span>{label}</span></div>;
}

export function Empty({ title, detail }: { title: string; detail?: string }) {
  return <div className="empty"><strong>{title}</strong>{detail && <span>{detail}</span>}</div>;
}

export function StatusPill({ status }: { status: string }) {
  const labels: Record<string, string> = {
    pending_approval: "待审批",
    draft: "草稿",
    dispatched: "运输中",
    received: "已入库",
    cancelled: "已取消",
    rejected: "已驳回",
    occupied: "占用中",
    consumed: "已消耗",
    recovered: "已回收",
    released: "已释放",
    pending: "待盘点",
    in_progress: "盘点中",
    submitted: "待复核",
    closed: "已关闭",
  };
  return <span className={`status status-${status}`}>{labels[status] || status}</span>;
}

export function formatDate(value?: string | null, withTime = true) {
  if (!value) return "-";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: withTime ? "2-digit" : undefined,
    minute: withTime ? "2-digit" : undefined,
  }).format(new Date(value));
}

export function Field({ label, children, hint }: PropsWithChildren<{ label: string; hint?: string }>) {
  return <label className="field"><span className="field-label">{label}</span>{children}{hint && <small>{hint}</small>}</label>;
}

export function SectionHeader({ title, subtitle, actions }: { title: string; subtitle?: string; actions?: ReactNode }) {
  return <div className="section-header"><div><h1>{title}</h1>{subtitle && <p>{subtitle}</p>}</div>{actions && <div className="section-actions">{actions}</div>}</div>;
}

export function showError(error: unknown): string {
  return error instanceof Error ? error.message : "操作失败";
}
