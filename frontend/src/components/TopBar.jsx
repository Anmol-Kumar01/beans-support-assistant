import { useEffect, useRef, useState } from "react";
import { ChevronDown, Menu, Moon, Sun } from "lucide-react";
import logo from "../assets/beans-logo.png";
import logoDark from "../assets/beans-logo-dark.png";
import icon from "../assets/beans-icon.png";
import { DemoButton } from "./ui.jsx";

export default function TopBar({ theme, userName, demoUrl, section, onToggleTheme, onOpenMenu, onClearHistory }) {
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef(null);

  useEffect(() => {
    if (!menuOpen) return;
    const close = (e) => !menuRef.current?.contains(e.target) && setMenuOpen(false);
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [menuOpen]);

  return (
    <header className="topbar">
      <button type="button" className="icon-btn topbar-menu" onClick={onOpenMenu} aria-label="Open menu">
        <Menu size={22} />
      </button>
      <img className="topbar-icon" src={theme === "dark" ? logoDark : logo} alt="Beans.ai" />
      <div className="topbar-title">
        <img className="topbar-avatar" src={icon} alt="" />
        <div className="topbar-title-text">
          <span className="topbar-name">Beans Assistant <span className="topbar-online"><i /> Online</span></span>
          {section && <span className="topbar-section">{section}</span>}
        </div>
      </div>

      <div className="topbar-actions">
        <DemoButton href={demoUrl} compact />
        <button
          type="button"
          className="icon-btn"
          onClick={onToggleTheme}
          aria-label={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"}
        >
          {theme === "dark" ? <Moon size={24} strokeWidth={1.7} /> : <Sun size={24} strokeWidth={1.7} />}
        </button>
        <div className="user-menu" ref={menuRef}>
          <button type="button" className="user-menu-btn" onClick={() => setMenuOpen((o) => !o)} aria-expanded={menuOpen}>
            <span className="avatar avatar-blue">{userName.charAt(0).toUpperCase()}</span>
            <ChevronDown size={20} className="user-menu-chevron" />
          </button>
          {menuOpen && (
            <div className="user-menu-panel" role="menu">
              <div className="user-menu-name">{userName}</div>
              <button
                type="button"
                role="menuitem"
                onClick={() => {
                  onClearHistory();
                  setMenuOpen(false);
                }}
              >
                Clear recent chats
              </button>
            </div>
          )}
        </div>
      </div>
    </header>
  );
}
