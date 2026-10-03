import { useEffect, useMemo, useState } from "react";
import { ExternalLink, Search } from "lucide-react";
import { getSources } from "../lib/api.js";
import { SOURCE_LABELS } from "./Chat.jsx";

// Searchable list of knowledge-base documents of the given source types.
export function SourceList({ types, onAsk, placeholder = "Search" }) {
  const [items, setItems] = useState(null);
  const [error, setError] = useState(null);
  const [query, setQuery] = useState("");

  useEffect(() => {
    setItems(null);
    setError(null);
    Promise.all(types.map((t) => getSources(t)))
      .then((lists) => setItems(lists.flat().sort((a, b) => a.title.localeCompare(b.title))))
      .catch(() => setError("Couldn't load sources. Is the server running?"));
  }, [types.join(",")]); // eslint-disable-line react-hooks/exhaustive-deps

  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    return (items || []).filter((s) => !q || s.title.toLowerCase().includes(q) || s.excerpt?.toLowerCase().includes(q));
  }, [items, query]);

  return (
    <>
      <div className="list-tools">
        <label className="search">
          <Search size={16} />
          <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder={placeholder} />
        </label>
        {items && <span className="page-status">{shown.length} of {items.length}</span>}
      </div>
      {error && <p className="page-status">{error}</p>}
      {!items && !error && <p className="page-status">Loading…</p>}
      <ul className="sources-grid">
        {shown.map((s) => (
          <li key={s.document_id} className="source-tile">
            <span className="source-tag">{SOURCE_LABELS[s.source_type]}</span>
            <h2>{s.title}</h2>
            {s.excerpt && <p>{s.excerpt}</p>}
            <div className="source-tile-actions">
              {s.url && (
                <a href={s.url} target="_blank" rel="noopener noreferrer">
                  Open <ExternalLink size={14} />
                </a>
              )}
              <button type="button" onClick={() => onAsk(`Tell me about "${s.title}"`)}>Ask about this</button>
            </div>
          </li>
        ))}
      </ul>
    </>
  );
}
