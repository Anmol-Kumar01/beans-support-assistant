import { CalendarDays, ChevronRight, CircleHelp, CodeXml, Compass, FileText, GraduationCap, Map, MessageSquare, Plug, Plus, X } from "lucide-react";
import logo from "../assets/beans-logo.png";
import logoDark from "../assets/beans-logo-dark.png";
import { timeAgo } from "../lib/history.js";

export const HELP_CENTER_URL = "https://beansai.zendesk.com/hc/en-us";

const EXPLORE = [
  { id: "explore", label: "Overview", icon: Compass },
  { id: "apis", label: "APIs & Docs", icon: CodeXml },
  { id: "maps", label: "Maps & Traffic", icon: Map },
  { id: "integrations", label: "Integrations", icon: Plug },
  { id: "tutorials", label: "Tutorials", icon: GraduationCap },
  { id: "articles", label: "Articles", icon: FileText },
  { id: "releases", label: "Release Notes", icon: CalendarDays },
];

export default function Sidebar({ view, chats, activeId, userName, theme, open, onClose, onNewChat, onOpenChat, onNavigate }) {
  return (
    <>
      <div className={`scrim${open ? " is-open" : ""}`} onClick={onClose} aria-hidden="true" />
      <aside className={`sidebar${open ? " is-open" : ""}`}>
        <div className="sidebar-head">
          <img className="sidebar-logo" src={theme === "dark" ? logoDark : logo} alt="Beans.ai" />
          <button type="button" className="icon-btn sidebar-close" onClick={onClose} aria-label="Close menu">
            <X size={20} />
          </button>
        </div>

        <button type="button" className="new-chat" onClick={onNewChat}>
          <Plus size={20} strokeWidth={2.2} /> New Chat
        </button>

        <nav className="nav">
          <button type="button" className={`nav-item${view === "chat" ? " is-active" : ""}`} onClick={() => onNavigate("chat")}>
            <MessageSquare size={20} strokeWidth={1.8} /> Chat
          </button>
          <p className="nav-label">Explore Beans</p>
          {EXPLORE.map(({ id, label, icon: Icon }) => (
            <button
              type="button"
              key={id}
              className={`nav-item nav-item-sm${view === id || (id === "tutorials" && view === "videos") ? " is-active" : ""}`}
              onClick={() => onNavigate(id)}
            >
              <Icon size={19} strokeWidth={1.8} /> {label}
            </button>
          ))}
          <a className="nav-item nav-item-sm" href={HELP_CENTER_URL} target="_blank" rel="noopener noreferrer">
            <CircleHelp size={19} strokeWidth={1.8} /> Help Center
          </a>
        </nav>

        <div className="sidebar-divider" />

        <div className="recent">
          <h2 className="recent-title">Recent Chats</h2>
          {chats.length === 0 ? (
            <p className="recent-empty">Your questions will appear here.</p>
          ) : (
            <ul className="recent-list">
              {chats.map((c) => (
                <li key={c.id}>
                  <button
                    type="button"
                    className={`recent-item${c.id === activeId && view === "chat" ? " is-active" : ""}`}
                    onClick={() => onOpenChat(c.id)}
                    title={c.title}
                  >
                    <MessageSquare size={22} strokeWidth={1.7} className="recent-icon" />
                    <span className="recent-text">
                      <span className="recent-name">{c.title}</span>
                      <span className="recent-time">{timeAgo(c.updatedAt)}</span>
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>

        <div className="sidebar-user">
          <span className="avatar avatar-teal">{userName.charAt(0).toUpperCase()}</span>
          <span className="sidebar-user-name">{userName}</span>
          <ChevronRight size={20} className="sidebar-user-chevron" />
        </div>
      </aside>
    </>
  );
}
