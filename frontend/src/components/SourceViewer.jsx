// Opening a source inside the app: articles (with numbered images), tutorials and release
// notes (Trainn player), training videos (YouTube at the cited moment), API endpoints, and
// product info. Every view has "Open in new tab" at the top.
import { useEffect, useState } from "react";
import { ChevronLeft, ChevronRight, X } from "lucide-react";
import { LinkButton, PageState, useHub } from "./ui.jsx";
import { Endpoint } from "../pages/ApiDocs.jsx";
import { getDocument } from "../lib/api.js";

function formatDay(iso) {
  if (!iso) return "";
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(y, m - 1, d).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

function ImageViewer({ images, index, onIndex, onClose }) {
  const count = images.length;
  const go = (step) => onIndex((index + step + count) % count);
  const img = images[index];
  return (
    <div className="viewer" role="dialog" aria-modal="true" aria-label={`Image ${index + 1} of ${count}`} onClick={onClose}>
      <div className="viewer-top" onClick={(e) => e.stopPropagation()}>
        <span className="viewer-count">{index + 1} / {count}</span>
        <button type="button" className="viewer-btn" onClick={onClose} aria-label="Close image"><X size={22} /></button>
      </div>
      {count > 1 && (
        <button type="button" className="viewer-btn viewer-prev" onClick={(e) => { e.stopPropagation(); go(-1); }} aria-label="Previous image">
          <ChevronLeft size={28} />
        </button>
      )}
      <img className="viewer-img" src={img.src} alt={img.alt} onClick={(e) => e.stopPropagation()} />
      {count > 1 && (
        <button type="button" className="viewer-btn viewer-next" onClick={(e) => { e.stopPropagation(); go(1); }} aria-label="Next image">
          <ChevronRight size={28} />
        </button>
      )}
    </div>
  );
}

export function ArticleReader({ article, onClose, onAsk }) {
  const [viewing, setViewing] = useState(null); // index of the image open in the viewer
  useEffect(() => setViewing(null), [article]);
  useEffect(() => {
    const onKey = (e) => {
      if (viewing === null) {
        if (e.key === "Escape") onClose();
        return;
      }
      const n = article.images.length;
      if (e.key === "Escape") setViewing(null);
      else if (e.key === "ArrowRight") setViewing((i) => (i + 1) % n);
      else if (e.key === "ArrowLeft") setViewing((i) => (i - 1 + n) % n);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose, viewing, article]);
  if (!article) return null;
  return (
    <div className="modal-scrim" onClick={() => viewing === null && onClose()}>
      <div className="modal modal-article" role="dialog" aria-modal="true" aria-label={article.title} onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <h2>{article.title}</h2>
          <div className="modal-actions">
            {article.url && <LinkButton href={article.url}>Open in new tab</LinkButton>}
            <button type="button" className="icon-btn" onClick={onClose} aria-label="Close"><X size={20} /></button>
          </div>
        </div>
        <div className="article-body">
          <div className="tile-row">
            <span className="tag">{article.group}</span>
            {article.updated && <span className="tag tag-soft">Updated {formatDay(article.updated)}</span>}
          </div>
          <div className="article-text">{article.text}</div>
          {article.images.length > 0 && (
            <>
              <h3 className="sub-title">Images <span className="count">{article.images.length}</span></h3>
              <div className="article-images">
                {article.images.map((img, i) => (
                  <button key={img.src} type="button" className="article-image" onClick={() => setViewing(i)} aria-label={`Open image ${i + 1}`}>
                    <span className="article-image-n">{i + 1}</span>
                    <img src={img.src} alt={img.alt} loading="lazy" />
                  </button>
                ))}
              </div>
            </>
          )}
          <div className="tile-foot">
            <button type="button" className="btn btn-ghost btn-compact" onClick={() => { onClose(); onAsk(`Tell me about "${article.title}"`); }}>
              Ask the assistant about this
            </button>
          </div>
        </div>
      </div>
      {viewing !== null && (
        <ImageViewer images={article.images} index={viewing} onIndex={setViewing} onClose={() => setViewing(null)} />
      )}
    </div>
  );
}


function useEscape(onClose) {
  useEffect(() => {
    const onKey = (e) => e.key === "Escape" && onClose();
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);
}

function Shell({ title, url, onClose, frame, children }) {
  useEscape(onClose);
  return (
    <div className="modal-scrim" onClick={onClose}>
      <div className={`modal${frame ? "" : " modal-article"}`} role="dialog" aria-modal="true" aria-label={title} onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <h2>{title}</h2>
          <div className="modal-actions">
            {url && <LinkButton href={url}>Open in new tab</LinkButton>}
            <button type="button" className="icon-btn" onClick={onClose} aria-label="Close"><X size={20} /></button>
          </div>
        </div>
        {children}
      </div>
    </div>
  );
}

function youtubeEmbed(url) {
  try {
    const u = new URL(url);
    const id = u.searchParams.get("v") || u.pathname.split("/").pop();
    const start = parseInt((u.searchParams.get("t") || "0").replace(/s$/, ""), 10) || 0;
    return `https://www.youtube.com/embed/${id}?start=${start}&autoplay=1&rel=0`;
  } catch {
    return null;
  }
}

function ArticleSource({ source, onClose, onAsk }) {
  const { data, error } = useHub("articles");
  const id = source.document_id.split(":")[1];
  const article = data?.items.find((a) => a.id === id);
  if (article) return <ArticleReader article={article} onClose={onClose} onAsk={onAsk} />;
  return (
    <Shell title={source.title} url={source.url} onClose={onClose}>
      <div className="article-body">{data && !article ? <DocumentText source={source} /> : <PageState error={error} />}</div>
    </Shell>
  );
}

function ApiSource({ source, onClose }) {
  const { data, error } = useHub("api");
  const content = useHub("content").data;
  const id = source.document_id.split(":")[1];
  const ep = data?.groups.flatMap((g) => g.endpoints).find((e) => e.id === id);
  // Endpoints have no page of their own: "Open in new tab" goes to the Route API docs.
  const docsUrl = content?.apis?.find((a) => a.reference)?.url;
  return (
    <Shell title={source.title} url={docsUrl} onClose={onClose}>
      <div className="article-body">
        {ep ? <Endpoint ep={ep} /> : data ? <DocumentText source={source} /> : <PageState error={error} />}
      </div>
    </Shell>
  );
}

function DocumentText({ source }) {
  const [doc, setDoc] = useState(null);
  const [error, setError] = useState(null);
  useEffect(() => {
    getDocument(source.document_id).then(setDoc).catch(() => setError("Couldn't load this source."));
  }, [source.document_id]);
  if (!doc) return <PageState error={error} />;
  return <div className="article-text">{doc.text}</div>;
}

export function SourceViewer({ source, onClose, onAsk }) {
  if (!source) return null;
  const { source_type: type, url, title } = source;
  if (type === "zendesk") return <ArticleSource source={source} onClose={onClose} onAsk={onAsk} />;
  if (type === "api_reference") return <ApiSource source={source} onClose={onClose} />;
  const embed = type === "youtube" ? youtubeEmbed(url) : (type === "trainn" || type === "release_note") ? url : null;
  if (embed) {
    return (
      <Shell title={title} url={url} onClose={onClose} frame>
        <iframe className="modal-frame" src={embed} title={title} allow="autoplay; fullscreen; clipboard-write; encrypted-media" allowFullScreen />
      </Shell>
    );
  }
  return (
    <Shell title={title} url={url} onClose={onClose}>
      <div className="article-body"><DocumentText source={source} /></div>
    </Shell>
  );
}
