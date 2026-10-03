import { useMemo, useState } from "react";
import { BookOpen, CircleCheck, CodeXml, Download, Gauge, Infinity as InfinityIcon, Search } from "lucide-react";
import { CopyButton, DemoButton, LinkButton, Markdown, PageHeader, PageState, Section, useHub } from "../components/ui.jsx";

function Method({ method }) {
  return <span className={`method method-${method.toLowerCase()}`}>{method}</span>;
}

export function Endpoint({ ep }) {
  return (
    <article className="endpoint">
      <h3 className="endpoint-name">{ep.name}</h3>
      <div className="endpoint-url">
        <Method method={ep.method} />
        <code>{ep.path}</code>
        <CopyButton text={ep.url} label="Copy URL" />
      </div>
      {ep.description && <Markdown>{ep.description}</Markdown>}
      {ep.query.length > 0 && (
        <>
          <h4 className="endpoint-label">Query parameters</h4>
          <div className="table-wrap">
            <table className="table table-sm">
              <thead><tr><th>Name</th><th>Example</th><th>Description</th></tr></thead>
              <tbody>
                {ep.query.map((q) => (
                  <tr key={q.key}><td><code>{q.key}</code></td><td><code>{q.value}</code></td><td>{q.description}</td></tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
      {ep.headers.length > 0 && (
        <>
          <h4 className="endpoint-label">Headers</h4>
          <div className="table-wrap">
            <table className="table table-sm">
              <tbody>{ep.headers.map((h) => <tr key={h.key}><td><code>{h.key}</code></td><td><code>{h.value}</code></td></tr>)}</tbody>
            </table>
          </div>
        </>
      )}
      {ep.body && (
        <>
          <div className="endpoint-label-row">
            <h4 className="endpoint-label">Request body</h4>
            <CopyButton text={ep.body} />
          </div>
          <pre className="code"><code>{ep.body}</code></pre>
        </>
      )}
    </article>
  );
}

function Reference({ api }) {
  const [selected, setSelected] = useState({ group: api.groups[0]?.id, endpoint: null });
  const [query, setQuery] = useState("");

  const groups = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return api.groups;
    return api.groups
      .map((g) => ({ ...g, endpoints: g.endpoints.filter((e) => `${e.method} ${e.name} ${e.path}`.toLowerCase().includes(q)) }))
      .filter((g) => g.endpoints.length || g.name.toLowerCase().includes(q));
  }, [api, query]);

  const group = api.groups.find((g) => g.id === selected.group);
  const endpoint = group?.endpoints.find((e) => e.id === selected.endpoint);

  return (
    <div className="ref">
      <nav className="ref-nav">
        <label className="search">
          <Search size={16} />
          <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search endpoints" />
        </label>
        {groups.map((g) => (
          <div key={g.id} className="ref-group">
            <button
              type="button"
              className={`ref-group-btn${selected.group === g.id && !selected.endpoint ? " is-active" : ""}`}
              onClick={() => setSelected({ group: g.id, endpoint: null })}
            >
              {g.name}
            </button>
            <ul>
              {g.endpoints.map((e) => (
                <li key={e.id}>
                  <button
                    type="button"
                    className={`ref-ep-btn${selected.endpoint === e.id ? " is-active" : ""}`}
                    onClick={() => setSelected({ group: g.id, endpoint: e.id })}
                  >
                    <Method method={e.method} /> <span>{e.name}</span>
                  </button>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </nav>
      <div className="ref-body">
        {endpoint ? (
          <Endpoint ep={endpoint} />
        ) : group ? (
          <article className="endpoint">
            <h3 className="endpoint-name">{group.name}</h3>
            <Markdown>{group.description}</Markdown>
            <h4 className="endpoint-label">Endpoints</h4>
            <ul className="ep-list">
              {group.endpoints.map((e) => (
                <li key={e.id}>
                  <button type="button" onClick={() => setSelected({ group: group.id, endpoint: e.id })}>
                    <Method method={e.method} /> <code>{e.path}</code> <span>{e.name}</span>
                  </button>
                </li>
              ))}
            </ul>
          </article>
        ) : null}
      </div>
    </div>
  );
}

export default function ApiDocs() {
  const { data: c, error } = useHub("content");
  const { data: api, error: apiError } = useHub("api");
  if (!c) return <main className="page"><PageState error={error} /></main>;

  return (
    <main className="page">
      <PageHeader
        kicker="APIs & Docs"
        title={<>Build with <span>Beans</span> APIs</>}
        text="Enterprise APIs for routing, maps and address intelligence, with tutorials and a full Route API reference."
      >
        <a className="btn btn-primary" href="/v1/hub/api/collection.json" download><Download size={17} /> Postman collection</a>
        <LinkButton href={c.developer_tutorials.url}>Tutorials on GitHub</LinkButton>
      </PageHeader>

      <Section title="APIs">
        <div className="grid grid-3">
          {c.apis.map((a) => (
            <article key={a.id} className="tile">
              <span className="tile-icon tone-blue"><CodeXml size={22} /></span>
              <h3 className="tile-title">{a.name} <span className="badge">{a.version}</span></h3>
              {a.text && <p className="tile-text">{a.text}</p>}
              {a.includes && <ul className="chips chips-sm">{a.includes.map((x) => <li key={x} className="chip">{x}</li>)}</ul>}
              <div className="tile-foot">
                <LinkButton href={a.url}>Docs</LinkButton>
                {a.reference && api && <a className="btn btn-ghost" href="#reference">Reference ↓</a>}
              </div>
            </article>
          ))}
        </div>
      </Section>

      <Section title="Developer tutorials" text="Step-by-step code tutorials in the beans-tutorials repository.">
        <div className="grid grid-5">
          {c.developer_tutorials.items.map((t) => (
            <a key={t.name} className="tile tile-link tile-compact" href={t.url} target="_blank" rel="noopener noreferrer">
              <span className="tile-icon tone-yellow"><BookOpen size={20} /></span>
              <h3 className="tile-title">{t.name}</h3>
            </a>
          ))}
        </div>
      </Section>

      <Section id="reference" title="Route API reference" text={api ? `${api.endpoint_count} endpoints · Base URL ${api.base_url}` : null}>
        {api ? (
          <>
            <details className="getting-started">
              <summary>Getting started: authentication, base URL and setup</summary>
              <Markdown>{api.description}</Markdown>
            </details>
            <Reference api={api} />
          </>
        ) : (
          <PageState error={apiError} />
        )}
      </Section>

      {c.api_plans && <ApiPlans plans={c.api_plans} demoUrl={c.demo_url} />}
    </main>
  );
}

function ApiPlans({ plans: m, demoUrl }) {
  const u = m.ultimate;
  return (
    <Section title="Pricing" action={<LinkButton href={m.url}>beans.ai</LinkButton>}>
      <div className="plans plans-4">
        {m.plans.map((p) => (
          <article key={p.name} className="plan">
            <h3 className="plan-name">{p.name}</h3>
            <p className="tile-text">{p.text}</p>
            <p className="plan-price">{p.price}</p>
            <ul className="check-list">
              <li><Gauge size={15} /> {p.qps}</li>
              <li><InfinityIcon size={15} /> {p.calls}</li>
            </ul>
          </article>
        ))}
      </div>
      <article className="plan plan-ultimate">
        <div className="plan-ultimate-copy">
          <h3 className="plan-name">{u.name}</h3>
          <p className="tile-text">{u.text}</p>
          <ul className="chips chips-sm">{u.includes.map((x) => <li key={x} className="chip"><CircleCheck size={13} /> {x}</li>)}</ul>
        </div>
        <div className="plan-ultimate-buy">
          <p className="plan-price plan-price-lg">{u.price}</p>
          <span className="tile-text">{u.calls}</span>
          <DemoButton href={demoUrl} label="Get started" />
        </div>
      </article>
    </Section>
  );
}
