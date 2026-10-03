import { ArrowRight, CalendarDays, CircleHelp, FileText, Play } from "lucide-react";
import icon from "../assets/beans-icon.png";

const CARDS = [
  { title: "Product Guides", text: "Step-by-step instructions and how-to guides", icon: FileText, tone: "teal", go: "articles" },
  { title: "Video Tutorials", text: "Watch training videos with timestamps", icon: Play, tone: "pink", go: "videos" },
  { title: "Release Notes", text: "What's new in Beans Route", icon: CalendarDays, tone: "yellow", go: "releases" },
  { title: "Troubleshooting", text: "Find solutions to common issues", icon: CircleHelp, tone: "blue", prefill: "I need help with a problem: " },
];

// Real user questions from support chat logs (wording cleaned up) that the knowledge base
// answers well. "How do drivers clock out?" is the most asked but has no help article yet.
const SUGGESTIONS = [
  "How do I use Lasso?",
  "How do I generate an MMR report?",
  "How do I request time off?",
  "How do I upload a FedEx manifest?",
  "How do I add a driver to my account?",
  "How do I fix sign-in issues?",
];

function Sparkle() {
  return (
    <svg className="sparkle" viewBox="0 0 40 40" aria-hidden="true">
      <path d="M16 5c1.5 9 5.2 12.7 14.2 14.2-9 1.5-12.7 5.2-14.2 14.2C14.5 24.4 10.8 20.7 1.8 19.2 10.8 17.7 14.5 14 16 5z" />
      <path d="M32 2c.7 4.2 2.4 5.9 6.6 6.6-4.2.7-5.9 2.4-6.6 6.6-.7-4.2-2.4-5.9-6.6-6.6 4.2-.7 5.9-2.4 6.6-6.6z" />
    </svg>
  );
}

function HeroLogo() {
  return (
    <div className="hero-logo">
      <svg className="hero-dashes" viewBox="0 0 260 220" aria-hidden="true">
        <g strokeLinecap="round" strokeWidth="5" fill="none">
          <path d="M44 22l10 18" stroke="#FFC107" />
          <path d="M18 48l20 7" stroke="#FFC107" />
          <path d="M222 168l18 9" stroke="#13B5A6" />
          <path d="M210 186l4 18" stroke="#13B5A6" />
        </g>
      </svg>
      <img src={icon} alt="" />
    </div>
  );
}

export default function Welcome({ onAsk, onNavigate, onPrefill }) {
  return (
    <div className="welcome">
      <section className="hero">
        <div className="hero-blob hero-blob-mint" />
        <div className="hero-blob hero-blob-pink" />
        <div className="hero-blob hero-blob-peach" />
        <div className="hero-copy">
          <p className="hero-kicker"><Sparkle /> Hello! I'm the</p>
          <h1 className="hero-title">Beans <span>Support</span> Assistant</h1>
          <p className="hero-text">
            Get instant answers about Beans Route. I can help with product features, setup guides,
            troubleshooting, and more.
          </p>
        </div>
        <HeroLogo />
      </section>

      <div className="cards">
        {CARDS.map(({ title, text, icon: Icon, tone, go, prefill }) => (
          <button
            type="button"
            key={title}
            className="card"
            onClick={() => (go ? onNavigate(go) : onPrefill(prefill))}
          >
            <span className={`card-icon tone-${tone}`}><Icon size={24} strokeWidth={1.9} /></span>
            <span className="card-body">
              <span className="card-title">{title}</span>
              <span className="card-text">{text}</span>
            </span>
          </button>
        ))}
      </div>

      <div className="suggest">
        <h2 className="suggest-title">Try asking something like:</h2>
        <div className="suggest-grid">
          {SUGGESTIONS.map((q) => (
            <button type="button" key={q} className="suggest-pill" onClick={() => onAsk(q)}>
              <span>{q}</span>
              <ArrowRight size={22} strokeWidth={2} />
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
