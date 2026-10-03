import { CircleCheck, Mail } from "lucide-react";
import { DemoButton, PageHeader, PageState, Section, useHub } from "../components/ui.jsx";
import { ContactBand } from "./Explore.jsx";

export default function Integrations() {
  const { data: c, error } = useHub("content");
  if (!c) return <main className="page"><PageState error={error} /></main>;
  const s = c.servicenow;

  return (
    <main className="page">
      <PageHeader kicker="Integrations" title={<>Beans.ai + <span>ServiceNow</span> FSM</>} text={`${s.tagline} ${s.intro}`}>
        <a className="btn btn-primary" href={`mailto:${c.contacts.sales}`}><Mail size={17} /> Contact sales</a>
        <DemoButton href={c.demo_url} />
      </PageHeader>

      <Section title="How it works" text={s.availability}>
        <div className="note-box">
          <strong>What you need:</strong> {s.requirements.join(" · ")}
        </div>
        <ol className="steps">
          {s.steps.map((step, i) => (
            <li key={step.title} className="step">
              <span className={`step-n tone-n${i % 4}`}>{i + 1}</span>
              <div>
                <h3>{step.title}</h3>
                <p>{step.text}</p>
              </div>
            </li>
          ))}
        </ol>
      </Section>

      <Section title="Why Beans.ai + ServiceNow">
        <ul className="chips">
          {s.why.map((w) => <li key={w} className="chip"><CircleCheck size={16} /> {w}</li>)}
        </ul>
        <div className="grid grid-2 mt">
          {s.benefits.map((b) => (
            <article key={b.title} className="tile tile-flat">
              <h3 className="tile-title">{b.title}</h3>
              <p className="tile-text">{b.text}</p>
            </article>
          ))}
        </div>
      </Section>

      <Section title="Pricing" text={s.pricing_note}>
        <div className="grid grid-2">
          {s.pricing.map((p) => (
            <article key={p.name} className="tile price">
              <h3 className="tile-title">{p.name}</h3>
              <p className="price-value">{p.example}</p>
            </article>
          ))}
        </div>
      </Section>

      <Section title="Real-world usage">
        <div className="grid grid-3">
          {c.usage.map((u) => (
            <article key={u.name} className="stat">
              <span className="stat-name">{u.name}</span>
              <span className="stat-text">{u.text}</span>
            </article>
          ))}
        </div>
      </Section>

      <Section title="Switching is easy">
        <div className="grid grid-2">
          {s.switching.map((x) => (
            <article key={x.title} className="tile tile-flat">
              <h3 className="tile-title">{x.title}</h3>
              <p className="tile-text">{x.text}</p>
            </article>
          ))}
        </div>
      </Section>

      <ContactBand content={c} />
    </main>
  );
}
