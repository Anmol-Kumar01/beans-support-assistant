// Building blocks shared by the Explore pages.
import { useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { ArrowUpRight, CalendarCheck, Check, Copy, X } from "lucide-react";
import { getHub } from "../lib/api.js";

export function useHub(part) {
  const [state, setState] = useState({ data: null, error: null });
  useEffect(() => {
    let live = true;
    getHub(part)
      .then((data) => live && setState({ data, error: null }))
      .catch(() => live && setState({ data: null, error: "Couldn't load this page. Is the server running?" }));
    return () => { live = false; };
  }, [part]);
  return state;
}

export function PageState({ error }) {
  return <p className="page-status">{error || "Loading…"}</p>;
}

export function PageHeader({ kicker, title, text, children }) {
  return (
    <header className="page-hero">
      <div className="hero-blob hero-blob-mint" />
      <div className="hero-blob hero-blob-pink" />
      <div className="page-hero-copy">
        {kicker && <p className="page-kicker">{kicker}</p>}
        <h1 className="page-title">{title}</h1>
        {text && <p className="page-text">{text}</p>}
        {children && <div className="page-actions">{children}</div>}
      </div>
    </header>
  );
}

export function Section({ id, title, text, action, children }) {
  return (
    <section className="section" id={id}>
      <div className="section-head">
        <div>
          <h2 className="section-title">{title}</h2>
          {text && <p className="section-text">{text}</p>}
        </div>
        {action}
      </div>
      {children}
    </section>
  );
}

export function LinkButton({ href, children, variant = "light", icon = true }) {
  return (
    <a className={`btn btn-${variant}`} href={href} target="_blank" rel="noopener noreferrer">
      {children}
      {icon && <ArrowUpRight size={16} strokeWidth={2.2} />}
    </a>
  );
}

export function DemoButton({ href, label = "Book a demo", compact = false }) {
  if (!href) return null;
  return (
    <a className={`btn btn-demo${compact ? " btn-compact" : ""}`} href={href} target="_blank" rel="noopener noreferrer">
      <CalendarCheck size={compact ? 16 : 18} strokeWidth={2} /> {label}
    </a>
  );
}

export function Markdown({ children }) {
  return (
    <div className="md">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{ a: ({ node, ...props }) => <a {...props} target="_blank" rel="noopener noreferrer" /> }}
      >
        {children || ""}
      </ReactMarkdown>
    </div>
  );
}

export function CopyButton({ text, label = "Copy" }) {
  const [done, setDone] = useState(false);
  return (
    <button
      type="button"
      className="copy-btn"
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(text);
          setDone(true);
          setTimeout(() => setDone(false), 1400);
        } catch { /* clipboard blocked */ }
      }}
    >
      {done ? <Check size={14} /> : <Copy size={14} />} {done ? "Copied" : label}
    </button>
  );
}

export function TutorialPlayer({ tutorial, onClose }) {
  useEffect(() => {
    const onKey = (e) => e.key === "Escape" && onClose();
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);
  if (!tutorial) return null;
  return (
    <div className="modal-scrim" onClick={onClose}>
      <div className="modal" role="dialog" aria-modal="true" aria-label={tutorial.title} onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <h2>{tutorial.title}</h2>
          <div className="modal-actions">
            <LinkButton href={tutorial.url}>Open in new tab</LinkButton>
            <button type="button" className="icon-btn" onClick={onClose} aria-label="Close"><X size={20} /></button>
          </div>
        </div>
        <iframe className="modal-frame" src={tutorial.url} title={tutorial.title} allow="fullscreen; autoplay; clipboard-write" allowFullScreen />
      </div>
    </div>
  );
}
