import { useState } from "react";
import DOMPurify from "dompurify";
import { CalendarDays, ChevronDown, CirclePlay } from "lucide-react";
import { PageHeader, PageState, TutorialPlayer, useHub } from "../components/ui.jsx";

// Release-note steps are HTML from the CMS: sanitize, and open links in a new tab.
DOMPurify.addHook("afterSanitizeAttributes", (node) => {
  if (node.tagName === "A") {
    node.setAttribute("target", "_blank");
    node.setAttribute("rel", "noopener noreferrer");
  }
});
const clean = (html) => DOMPurify.sanitize(html, { USE_PROFILES: { html: true }, FORBID_TAGS: ["style", "form", "input"] });

function formatDate(iso) {
  if (!iso) return "";
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(y, m - 1, d).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

function Note({ note, onPlay }) {
  const [open, setOpen] = useState(false);
  return (
    <li className="release">
      <span className="release-dot" aria-hidden="true" />
      <article className="release-card">
        <div className="release-head">
          <div className="tile-row">
            {note.platform && <span className="tag">{note.platform}</span>}
            {note.audiences.map((a) => <span key={a} className="tag tag-soft">{a}</span>)}
          </div>
          {note.date && (
            <time className="release-date" dateTime={note.date}>
              <CalendarDays size={14} /> {formatDate(note.date)}
            </time>
          )}
        </div>
        <h2 className="release-title">{note.title}</h2>
        <p className="tile-text">{note.summary}</p>
        <div className="tile-foot">
          {note.steps_html.length > 0 && (
            <button type="button" className="btn btn-light btn-compact" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
              <ChevronDown size={16} className={open ? "rot" : ""} /> {open ? "Hide steps" : `Show ${note.steps_html.length} steps`}
            </button>
          )}
          {note.tutorial_url && (
            <button type="button" className="btn btn-primary btn-compact" onClick={() => onPlay({ title: note.title, url: note.tutorial_url })}>
              <CirclePlay size={16} /> Interactive tutorial
            </button>
          )}
        </div>
        {open && (
          <div className="release-steps">
            {note.video_url && <video className="release-video" src={note.video_url} poster={note.thumbnail_url || undefined} controls preload="none" />}
            {note.steps_html.map((html, i) => (
              <div key={i} className="release-step" dangerouslySetInnerHTML={{ __html: clean(html) }} />
            ))}
          </div>
        )}
      </article>
    </li>
  );
}

export default function ReleaseNotes() {
  const { data, error } = useHub("release-notes");
  const [playing, setPlaying] = useState(null);

  return (
    <main className="page">
      <PageHeader kicker="Release Notes" title={<>What's <span>new</span> in Beans Route</>} text="New features and improvements, with step-by-step guides." />
      <section className="section">
        {!data ? <PageState error={error} /> : (
          <ol className="releases">{data.map((n) => <Note key={n.id} note={n} onPlay={setPlaying} />)}</ol>
        )}
      </section>
      <TutorialPlayer tutorial={playing} onClose={() => setPlaying(null)} />
    </main>
  );
}
