import { ArrowRight, Award, CalendarDays, CircleCheck, CodeXml, FileText, GraduationCap, Mail, Map, MapPinned, MessageSquare, Phone, Plug, Truck, TrendingUp } from "lucide-react";
import { DemoButton, LinkButton, PageHeader, PageState, Section, useHub } from "../components/ui.jsx";

const PRODUCT_ICONS = { beansroute: Truck, beansai: MapPinned, api: CodeXml };

export default function Explore({ onNavigate }) {
  const { data: c, error } = useHub("content");
  const api = useHub("api").data;
  const tutorials = useHub("tutorials").data;
  const notes = useHub("release-notes").data;
  const articles = useHub("articles").data;
  if (!c) return <main className="page"><PageState error={error} /></main>;

  const sections = [
    { go: "apis", icon: CodeXml, tone: "blue", title: "APIs & Docs", text: "Route, Maps and Widget APIs with a full endpoint reference.", count: api && `${api.endpoint_count} endpoints` },
    { go: "maps", icon: Map, tone: "teal", title: "Maps & Traffic", text: "3D maps, address APIs, distance matrix with predictive and real-time traffic." },
    { go: "integrations", icon: Plug, tone: "pink", title: "Integrations", text: "Plug-and-play routing for ServiceNow Field Service Management." },
    { go: "tutorials", icon: GraduationCap, tone: "yellow", title: "Tutorials", text: "Interactive walkthroughs and training videos for every product.", count: tutorials && `${tutorials.items.length} tutorials` },
    { go: "releases", icon: CalendarDays, tone: "teal", title: "Release Notes", text: "What's new in Beans Route, step by step.", count: notes && `${notes.length} releases` },
    { go: "articles", icon: FileText, tone: "blue", title: "Articles", text: "Help-center articles for drivers, dispatchers and managers.", count: articles && `${articles.items.length} articles` },
  ];

  return (
    <main className="page">
      <PageHeader
        kicker="Explore Beans"
        title={<>Everything about <span>Beans</span>, in one place</>}
        text="Products, APIs, maps, integrations, tutorials and release notes — all in one hub."
      >
        <DemoButton href={c.demo_url} />
        <button type="button" className="btn btn-light" onClick={() => onNavigate("chat")}>
          <MessageSquare size={17} /> Ask the assistant
        </button>
      </PageHeader>

      <Section title="Products" text="Three ways to use Beans: run deliveries, use location intelligence, or build on the APIs.">
        <div className="products">
          {c.products.map((p, i) => <ProductCard key={p.id} product={p} tone={["teal", "pink", "blue"][i % 3]} demoUrl={c.demo_url} onNavigate={onNavigate} />)}
        </div>
        {c.trusted_by?.length > 0 && (
          <div className="trusted">
            <span className="trusted-label">Trusted by</span>
            {c.trusted_by.map((n) => <span key={n} className="trusted-name">{n}</span>)}
          </div>
        )}
      </Section>

      <Section title="Explore">
        <div className="grid grid-3">
          {sections.map(({ go, icon: Icon, tone, title, text, count }) => (
            <button type="button" key={go} className="tile tile-link" onClick={() => onNavigate(go)}>
              <span className={`tile-icon tone-${tone}`}><Icon size={22} /></span>
              <h3 className="tile-title">{title}</h3>
              <p className="tile-text">{text}</p>
              <span className="tile-foot tile-go">{count || "Open"} <ArrowRight size={16} /></span>
            </button>
          ))}
        </div>
      </Section>

      <Section title="Why Beans" text="How Beans compares with other mapping and routing APIs.">
        <div className="table-wrap">
          <table className="table">
            <thead><tr><th>Key advantage</th><th>Beans.ai</th><th>Others</th></tr></thead>
            <tbody>
              {c.why_beans.rows.map((r) => (
                <tr key={r.feature}>
                  <td className="table-strong">{r.feature}</td>
                  <td><span className="yes">✓</span> {r.beans}</td>
                  <td><span className="no">✕</span> {r.others}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="grid grid-2 mt">
          {c.why_beans.points.map((p) => (
            <article key={p.title} className="tile tile-flat">
              <h3 className="tile-title">{p.title}</h3>
              <p className="tile-text">{p.text}</p>
            </article>
          ))}
        </div>
      </Section>

      <Section title="Trusted at scale">
        <div className="grid grid-3">
          {c.usage.map((u) => (
            <article key={u.name} className="stat">
              <span className="stat-name">{u.name}</span>
              <span className="stat-text">{u.text}</span>
            </article>
          ))}
        </div>
      </Section>

      <ContactBand content={c} />
    </main>
  );
}

function ProductCard({ product: p, tone, demoUrl, onNavigate }) {
  const Icon = PRODUCT_ICONS[p.id] || MapPinned;
  const host = new URL(p.url).hostname.replace("www.", "");
  return (
    <article className={`product product-${tone}`}>
      <div className="product-main">
        <div className="product-head">
          <span className={`tile-icon tone-${tone}`}><Icon size={22} /></span>
          <div>
            <h3 className="product-name">{p.name}</h3>
            <p className="product-tagline">{p.tagline}</p>
          </div>
        </div>
        <p className="tile-text">{p.text}</p>
        {p.for?.length > 0 && (
          <div className="product-for">
            <span className="tile-label">Built for</span>
            <ul className="chips chips-sm">{p.for.map((f) => <li key={f} className="chip">{f}</li>)}</ul>
          </div>
        )}
        {p.stats?.length > 0 && (
          <ul className="product-stats">
            {p.stats.map((s) => (
              <li key={s.label}><strong>{s.value}</strong><span>{s.label}</span></li>
            ))}
          </ul>
        )}
        <div className="tile-foot">
          <LinkButton href={p.url} variant="primary">{host}</LinkButton>
          <DemoButton href={demoUrl} compact />
          {p.page && (
            <button type="button" className="btn btn-ghost btn-compact" onClick={() => onNavigate(p.page)}>
              {p.page_label} <ArrowRight size={15} />
            </button>
          )}
        </div>
      </div>
      <div className="product-side">
        <span className="tile-label">Key features</span>
        <ul className="feature-list">
          {p.features.map((f) => (
            <li key={f.name}>
              <CircleCheck size={16} />
              <span><strong>{f.name}</strong> {f.text}</span>
            </li>
          ))}
        </ul>
        {p.results?.length > 0 && (
          <>
            <span className="tile-label"><TrendingUp size={13} /> Customer results</span>
            <ul className="chips chips-sm">{p.results.map((r) => <li key={r} className="chip chip-teal">{r}</li>)}</ul>
          </>
        )}
        {p.awards?.length > 0 && (
          <>
            <span className="tile-label"><Award size={13} /> Awards</span>
            <ul className="chips chips-sm">{p.awards.map((a) => <li key={a} className="chip chip-yellow">{a}</li>)}</ul>
          </>
        )}
      </div>
    </article>
  );
}

export function ContactBand({ content: c }) {
  const k = c.contacts;
  return (
    <section className="cta-band">
      <div>
        <h2>See Beans in action</h2>
        <p>Book a demo, or talk to the team.</p>
        <ul className="contact-list">
          <li><Mail size={16} /> Sales: <a href={`mailto:${k.sales}`}>{k.sales}</a></li>
          <li><Mail size={16} /> Support: <a href={`mailto:${k.support}`}>{k.support}</a></li>
          {k.direct && <li><Mail size={16} /> Direct: <a href={`mailto:${k.direct}`}>{k.direct}</a></li>}
          <li><Phone size={16} /> <a href={`tel:${k.phone.replace(/[^+\d]/g, "")}`}>{k.phone}</a></li>
        </ul>
      </div>
      <DemoButton href={c.demo_url} />
    </section>
  );
}
