import { useEffect, useRef, useState } from 'react';
import type { ConditionAdapter } from './returnConditionAdapter';

type Link = { url: string; expires_at: string; index: number };
type Props = { adapter: Pick<ConditionAdapter, 'download'>; inbound: string; event: string; files: string[] };
export default function ConditionEventEvidence({ adapter, inbound, event, files }: Props) {
  const [link, setLink] = useState<Link | null>(null), [error, setError] = useState(''), [busy, setBusy] = useState(false);
  const generation = useRef(0), inFlight = useRef(false);
  useEffect(() => { generation.current++; setLink(null); setError(''); setBusy(false); inFlight.current = false;
    return () => { generation.current++; };
  }, [adapter, inbound, event, files]);
  useEffect(() => { if (!link) return;
    const timer = window.setTimeout(() => { setLink(null); setError('附件链接已过期，请重新核验。'); }, Math.max(0, Date.parse(link.expires_at) - Date.now()));
    return () => window.clearTimeout(timer);
  }, [link]);
  async function view(file: string, index: number) {
    if (inFlight.current) return;
    const captured = generation.current; inFlight.current = true; setBusy(true); setLink(null); setError('');
    try { const value = await adapter.download(inbound, event, file);
      if (captured !== generation.current) return;
      if (!Number.isFinite(Date.parse(value.expires_at)) || Date.parse(value.expires_at) <= Date.now()) throw new Error();
      setLink({ ...value, index });
    } catch { if (captured === generation.current) setError('附件暂不可查看，请重新核验案件及查看权限。'); }
    finally { if (captured === generation.current) { inFlight.current = false; setBusy(false); } }
  }
  return <div>
    {files.map((file, index) => <button key={file} disabled={busy} onClick={() => void view(file, index)}>核验附件 {index + 1}</button>)}
    {busy && <span role="status">正在核验附件…</span>}
    {error && <p role="alert">{error}</p>}
    {link && <a href={link.url} target="_blank" rel="noopener noreferrer" referrerPolicy="no-referrer"
      onClick={e => { if (Date.parse(link.expires_at) <= Date.now()) { e.preventDefault(); setLink(null); setError('附件链接已过期，请重新核验。'); } }}>打开附件 {link.index + 1}</a>}
  </div>;
}
