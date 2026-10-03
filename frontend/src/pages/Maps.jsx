import { ArrowUpRight, Box, CircleCheck, Clock, CodeXml, Gauge, MapPinned } from "lucide-react";
import { DemoButton, LinkButton, PageHeader, PageState, Section, useHub } from "../components/ui.jsx";

export default function Maps() {
  const { data: c, error } = useHub("content");
  if (!c) return <main className="page"><PageState error={error} /></main>;
  const t = c.traffic;
  const mapsApi = c.apis.find((a) => a.id === "maps-api");
  const widget = c.apis.find((a) => a.id === "maps-widget");
  const matrix = c.developer_tutorials.items.find((i) => i.name === "Distance Matrix");

  return (
    <main className="page">
      <PageHeader
        kicker="Maps & Traffic"
        title={<>Beans <span>Maps</span> and distance matrix</>}
        text="Hyper-accurate location data, 3D maps, address APIs and travel times with predictive and real-time traffic."
      >
        <LinkButton href={c.maps_url} variant="primary">Open 3D Maps</LinkButton>
        <DemoButton href={c.demo_url} />
      </PageHeader>

      <Section title="Beans Maps">
        <div className="grid grid-3">
          <article className="tile">
            <span className="tile-icon tone-teal"><Box size={22} /></span>
            <h3 className="tile-title">3D Maps</h3>
            <p className="tile-text">Explore Beans' 3D maps.</p>
            <div className="tile-foot"><LinkButton href={c.maps_url}>beans.ai/3dmaps</LinkButton></div>
          </article>
          {mapsApi && (
            <article className="tile">
              <span className="tile-icon tone-blue"><MapPinned size={22} /></span>
              <h3 className="tile-title">{mapsApi.name} <span className="badge">{mapsApi.version}</span></h3>
              <ul className="chips chips-sm">{mapsApi.includes.map((x) => <li key={x} className="chip">{x}</li>)}</ul>
              <div className="tile-foot"><LinkButton href={mapsApi.url}>API docs</LinkButton></div>
            </article>
          )}
          {widget && (
            <article className="tile">
              <span className="tile-icon tone-pink"><CodeXml size={22} /></span>
              <h3 className="tile-title">{widget.name} <span className="badge">{widget.version}</span></h3>
              <p className="tile-text">Embed Beans maps in your web app.</p>
              <div className="tile-foot"><LinkButton href={widget.url}>Widget docs</LinkButton></div>
            </article>
          )}
        </div>
      </Section>

      <Section
        title="Distance matrix & traffic"
        text={t.intro}
        action={matrix && <LinkButton href={matrix.url}>Distance Matrix tutorial</LinkButton>}
      >
        <div className="grid grid-2">
          {t.options.map((o, i) => (
            <a key={o.name} className="tile tile-link" href={o.url} target="_blank" rel="noopener noreferrer">
              <span className={`tile-icon ${i ? "tone-pink" : "tone-teal"}`}>{i ? <Gauge size={22} /> : <Clock size={22} />}</span>
              <h3 className="tile-title">{o.name}</h3>
              <p className="tile-sub">{o.tagline}</p>
              <p className="tile-text">{o.text}</p>
              <p className="tile-label">Best for</p>
              <ul className="check-list">{o.best_for.map((b) => <li key={b}><CircleCheck size={15} /> {b}</li>)}</ul>
              <p className="tile-label">Typical use case</p>
              <p className="tile-text">{o.example}</p>
              <p className="tile-note">{o.ready}</p>
              <span className="tile-foot tile-go">{o.link_label} <ArrowUpRight size={15} /></span>
            </a>
          ))}
        </div>

        <h3 className="sub-title">Which traffic when</h3>
        <div className="table-wrap">
          <table className="table">
            <thead><tr><th>Planning phase</th><th>Traffic type</th><th>Benefit</th></tr></thead>
            <tbody>
              {t.phases.map((p) => (
                <tr key={p.phase}><td className="table-strong">{p.phase}</td><td>{p.traffic}</td><td>{p.benefit}</td></tr>
              ))}
            </tbody>
          </table>
        </div>

        <h3 className="sub-title">Mix and match by</h3>
        <ul className="chips">{t.mix.map((m) => <li key={m} className="chip">{m}</li>)}</ul>

        <h3 className="sub-title">Plans</h3>
        <div className="grid grid-2">
          {t.plans.map((p) => (
            <a key={p.name} className="tile tile-link price" href={p.url} target="_blank" rel="noopener noreferrer">
              <h3 className="tile-title">{p.name}</h3>
              <p className="tile-text">{p.includes}</p>
              <p className="price-value">{p.price}</p>
              <span className="tile-foot tile-go">{p.link_label} <ArrowUpRight size={15} /></span>
            </a>
          ))}
        </div>
        <p className="fine-print">{t.plans_note}</p>
      </Section>

      {c.maps_plans && <MapsPlans plans={c.maps_plans} mapsUrl={c.maps_url} demoUrl={c.demo_url} />}
    </main>
  );
}

function PlanFeatures({ items }) {
  return <ul className="check-list">{items.map((f) => <li key={f}><CircleCheck size={15} /> {f}</li>)}</ul>;
}

function MapsPlans({ plans: m, mapsUrl, demoUrl }) {
  const u = m.ultimate;
  return (
    <Section title="Beans Maps pricing" text={m.title} action={<LinkButton href={mapsUrl}>beans.ai/3dmaps</LinkButton>}>
      <div className="plans">
        <article className="plan plan-core">
          <span className="plan-label">{m.core.label}</span>
          <h3 className="plan-name">{m.core.name}</h3>
          <p className="plan-price">{m.core.price}</p>
          <p className="tile-text">{m.core.text}</p>
          <PlanFeatures items={m.core.features} />
        </article>
        {m.addons.map((a) => (
          <article key={a.name} className="plan">
            <span className="plan-label">Add-on {a.n}</span>
            <h3 className="plan-name">{a.name}</h3>
            <p className="plan-price">{a.price}{a.price_note && <span> ({a.price_note})</span>}</p>
            <p className="tile-text">{a.text}</p>
            <PlanFeatures items={a.features} />
          </article>
        ))}
      </div>
      <article className="plan plan-ultimate">
        <div className="plan-ultimate-copy">
          <span className="plan-label">{u.label}</span>
          <h3 className="plan-name">{u.name}</h3>
          <p className="tile-text">{u.text}</p>
          <ul className="chips chips-sm">{u.includes.map((x) => <li key={x} className="chip">{x}</li>)}</ul>
        </div>
        <div className="plan-ultimate-buy">
          <p className="plan-price plan-price-lg">{u.price_was && <s>{u.price_was}</s>} {u.price}</p>
          <DemoButton href={demoUrl} label="Get started" />
        </div>
      </article>
    </Section>
  );
}
