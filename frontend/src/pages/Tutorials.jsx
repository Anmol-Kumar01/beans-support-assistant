import { useEffect, useMemo, useState } from "react";
import { BookOpen, CirclePlay, LayoutDashboard, Search, Smartphone, Store } from "lucide-react";
import { PageHeader, PageState, TutorialPlayer, useHub } from "../components/ui.jsx";
import { ArticleReader } from "../components/SourceViewer.jsx";
import { SourceList } from "../components/SourcesPage.jsx";

const PLATFORM_ICONS = { Dispatch: LayoutDashboard, "Mobile App": Smartphone, "Shipper Portal": Store };

function Interactive({ data }) {
  const [platform, setPlatform] = useState("All");
  const [query, setQuery] = useState("");
  const [playing, setPlaying] = useState(null);

  const platforms = ["All", ...new Set(data.items.map((t) => t.platform))];
  const byCategory = useMemo(() => {
    const q = query.trim().toLowerCase();
    const groups = new Map();
    for (const t of data.items) {
      if (platform !== "All" && t.platform !== platform) continue;
      if (q && !`${t.title} ${t.summary}`.toLowerCase().includes(q)) continue;
      if (!groups.has(t.category)) groups.set(t.category, []);
      groups.get(t.category).push(t);
    }
    return [...groups.entries()];
  }, [data, platform, query]);

  return (
    <>
      <div className="list-tools">
        <div className="segmented" role="tablist" aria-label="Platform">
          {platforms.map((p) => (
            <button key={p} type="button" role="tab" aria-selected={platform === p} className={platform === p ? "is-active" : ""} onClick={() => setPlatform(p)}>
              {p}
            </button>
          ))}
        </div>
        <label className="search">
          <Search size={16} />
          <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search tutorials" />
        </label>
      </div>

      {byCategory.length === 0 && <p className="page-status">No tutorials match.</p>}
      {byCategory.map(([category, items]) => (
        <section key={category} className="tut-group">
          <h2 className="sub-title">{category} <span className="count">{items.length}</span></h2>
          <ul className="grid grid-3">
            {items.map((t) => {
              const Icon = PLATFORM_ICONS[t.platform] || CirclePlay;
              return (
                <li key={t.id} className="tile">
                  <div className="tile-row">
                    <span className="tag"><Icon size={14} /> {t.platform}</span>
                    {t.audiences.map((a) => <span key={a} className="tag tag-soft">{a}</span>)}
                  </div>
                  <h3 className="tile-title">{t.title}</h3>
                  <p className="tile-text clamp-3">{t.summary}</p>
                  <div className="tile-foot">
                    <button type="button" className="btn btn-primary btn-compact" onClick={() => setPlaying(t)} disabled={!t.url}>
                      <CirclePlay size={16} /> Start tutorial
                    </button>
                  </div>
                </li>
              );
            })}
          </ul>
        </section>
      ))}
      {data.hidden_account_specific > 0 && (
        <p className="fine-print">{data.hidden_account_specific} tutorials made for specific customer accounts are not listed.</p>
      )}
      <TutorialPlayer tutorial={playing} onClose={() => setPlaying(null)} />
    </>
  );
}

const ARTICLE_GROUPS = ["All", "Drivers", "Managers", "Getting started", "General"];

function Articles({ data, onAsk }) {
  const [group, setGroup] = useState("All");
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(null);
  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    return data.items.filter((a) => (group === "All" || a.group === group) && (!q || `${a.title} ${a.text}`.toLowerCase().includes(q)));
  }, [data, group, query]);

  return (
    <>
      <div className="list-tools">
        <div className="segmented" role="tablist" aria-label="Audience">
          {ARTICLE_GROUPS.map((g) => (
            <button key={g} type="button" role="tab" aria-selected={group === g} className={group === g ? "is-active" : ""} onClick={() => setGroup(g)}>
              {g}
            </button>
          ))}
        </div>
        <label className="search">
          <Search size={16} />
          <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search articles" />
        </label>
      </div>
      <p className="page-status">{shown.length} of {data.items.length} articles</p>
      <ul className="grid grid-3 mt">
        {shown.map((a) => (
          <li key={a.id} className="tile tile-link" onClick={() => setOpen(a)} onKeyDown={(e) => e.key === "Enter" && setOpen(a)} tabIndex={0} role="button">
            <div className="tile-row">
              <span className="tag">{a.group}</span>
              {a.images.length > 0 && <span className="tag tag-soft">{a.images.length} screenshots</span>}
            </div>
            <h3 className="tile-title">{a.title}</h3>
            <p className="tile-text clamp-3">{a.excerpt}</p>
            <span className="tile-foot tile-go"><BookOpen size={15} /> Read article</span>
          </li>
        ))}
      </ul>
      <ArticleReader article={open} onClose={() => setOpen(null)} onAsk={onAsk} />
    </>
  );
}

const TABS = [
  { id: "interactive", label: "Interactive tutorials", part: "tutorials" },
  { id: "articles", label: "Articles", part: "articles" },
  { id: "videos", label: "Training videos" },
];

export default function Tutorials({ onAsk, initialTab = "interactive" }) {
  const [tab, setTab] = useState(initialTab);
  const { data, error } = useHub("tutorials");
  const articles = useHub("articles");
  useEffect(() => setTab(initialTab), [initialTab]);

  return (
    <main className="page">
      <PageHeader
        kicker="Tutorials"
        title={<>Learn <span>Beans Route</span> step by step</>}
        text="Interactive walkthroughs, help-center articles and training videos for the dispatch console, driver app and shipper portal."
      />
      <div className="tabs" role="tablist">
        {TABS.map((t) => {
          const count = t.id === "interactive" ? data?.items.length : t.id === "articles" ? articles.data?.items.length : null;
          return (
            <button key={t.id} type="button" role="tab" aria-selected={tab === t.id} className={tab === t.id ? "is-active" : ""} onClick={() => setTab(t.id)}>
              {t.label} {count != null && <span className="count">{count}</span>}
            </button>
          );
        })}
      </div>
      <section className="section">
        {tab === "interactive" && (data ? <Interactive data={data} /> : <PageState error={error} />)}
        {tab === "articles" && (articles.data ? <Articles data={articles.data} onAsk={onAsk} /> : <PageState error={articles.error} />)}
        {tab === "videos" && <SourceList types={["youtube"]} onAsk={onAsk} placeholder="Search videos" />}
      </section>
    </main>
  );
}
