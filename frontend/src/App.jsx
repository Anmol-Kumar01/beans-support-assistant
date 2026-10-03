import { useCallback, useEffect, useRef, useState } from "react";
import Sidebar from "./components/Sidebar.jsx";
import TopBar from "./components/TopBar.jsx";
import Welcome from "./components/Welcome.jsx";
import Chat from "./components/Chat.jsx";
import Composer from "./components/Composer.jsx";
import Explore from "./pages/Explore.jsx";
import ApiDocs from "./pages/ApiDocs.jsx";
import Maps from "./pages/Maps.jsx";
import Integrations from "./pages/Integrations.jsx";
import Tutorials from "./pages/Tutorials.jsx";
import ReleaseNotes from "./pages/ReleaseNotes.jsx";
import { askQuestion, getHealth, getHub } from "./lib/api.js";
import { SourceViewer } from "./components/SourceViewer.jsx";
import { loadChats, newId, saveChats } from "./lib/history.js";

// Light (white) by default; dark only when the user picks it with the sun/moon button.
const THEME_KEY = "beans.theme.choice";

const PAGE_NAMES = {
  explore: "Overview",
  apis: "APIs & Docs",
  maps: "Maps & Traffic",
  integrations: "Integrations",
  tutorials: "Tutorials",
  videos: "Tutorials",
  articles: "Articles",
  releases: "Release Notes",
};

function initialTheme() {
  try {
    return localStorage.getItem(THEME_KEY) === "dark" ? "dark" : "light";
  } catch {
    return "light";
  }
}

export default function App() {
  // chat | explore | apis | maps | integrations | tutorials | articles | videos | releases
  const [view, setView] = useState("chat");
  const [demoUrl, setDemoUrl] = useState(null);
  const [chats, setChats] = useState(loadChats);
  const [activeId, setActiveId] = useState(null);
  const [draft, setDraft] = useState("");
  const [theme, setTheme] = useState(initialTheme);
  const [userName, setUserName] = useState("Guest");
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [openSource, setOpenSource] = useState(null); // source card opened in the viewer
  const abortRef = useRef(null); // stops the answer that is streaming
  const inputRef = useRef(null);

  const active = chats.find((c) => c.id === activeId) || null;
  const busy = active?.messages.some((m) => m.status === "loading" || m.status === "streaming") ?? false;

  useEffect(() => saveChats(chats), [chats]);
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);

  const toggleTheme = () => setTheme((t) => {
    const next = t === "dark" ? "light" : "dark";
    try { localStorage.setItem(THEME_KEY, next); } catch { /* ignore */ }
    return next;
  });
  useEffect(() => {
    getHealth().then((h) => setUserName(h.user_name || "Guest")).catch(() => {});
    getHub("content").then((c) => setDemoUrl(c.demo_url || null)).catch(() => {});
  }, []);

  const updateMessage = useCallback((chatId, messageId, patch) => {
    setChats((all) => all.map((c) => (c.id !== chatId ? c : {
      ...c,
      updatedAt: Date.now(),
      messages: c.messages.map((m) => (m.id === messageId ? { ...m, ...patch } : m)),
    })));
  }, []);

  const ask = useCallback(async (question, chatId = activeId) => {
    const q = question.trim();
    if (!q) return;
    let id = chatId;
    const userMsg = { id: newId(), role: "user", text: q, time: Date.now() };
    const botMsg = { id: newId(), role: "bot", text: "", status: "loading", time: Date.now() };
    if (!id || !chats.some((c) => c.id === id)) {
      id = newId();
      setChats((all) => [{ id, title: q, updatedAt: Date.now(), messages: [userMsg, botMsg] }, ...all]);
    } else {
      setChats((all) => {
        const chat = all.find((c) => c.id === id);
        const rest = all.filter((c) => c.id !== id);
        return [{ ...chat, updatedAt: Date.now(), messages: [...chat.messages, userMsg, botMsg] }, ...rest];
      });
    }
    setActiveId(id);
    setView("chat");
    setDraft("");
    const controller = new AbortController();
    abortRef.current = controller;
    let partial = "";
    try {
      const final = await askQuestion(q, id, (text) => {
        partial = text;
        updateMessage(id, botMsg.id, { text, status: "streaming" });
      }, controller.signal);
      updateMessage(id, botMsg.id, {
        text: final.answer || "",
        sources: final.sources || [],
        evidenceStatus: final.evidence_status,
        status: "done",
        time: Date.now(),
      });
    } catch (err) {
      if (err.name === "AbortError") {
        updateMessage(id, botMsg.id, { text: partial || "_Stopped before the answer started._", sources: [], status: "done", stopped: true, time: Date.now() });
      } else {
        updateMessage(id, botMsg.id, { status: "error", error: err.message, question: q });
      }
    } finally {
      if (abortRef.current === controller) abortRef.current = null;
    }
  }, [activeId, chats, updateMessage]);

  const retry = (botMsg) => {
    setChats((all) => all.map((c) => {
      if (c.id !== activeId) return c;
      const i = c.messages.findIndex((m) => m.id === botMsg.id);
      return { ...c, messages: c.messages.slice(0, Math.max(0, i - 1)) };
    }));
    ask(botMsg.question, activeId);
  };

  const newChat = () => {
    setActiveId(null);
    setView("chat");
    setDraft("");
    setSidebarOpen(false);
    setTimeout(() => inputRef.current?.focus(), 0);
  };

  const openChat = (id) => {
    setActiveId(id);
    setView("chat");
    setSidebarOpen(false);
  };

  const navigate = (next) => {
    setView(next);
    setSidebarOpen(false);
  };

  const prefill = (text) => {
    setDraft(text);
    setTimeout(() => inputRef.current?.focus(), 0);
  };

  const clearHistory = () => {
    setChats([]);
    setActiveId(null);
  };

  const showWelcome = view === "chat" && !active;

  return (
    <div className="app">
      <Sidebar
        view={view}
        chats={chats}
        activeId={activeId}
        userName={userName}
        theme={theme}
        open={sidebarOpen}
        onClose={() => setSidebarOpen(false)}
        onNewChat={newChat}
        onOpenChat={openChat}
        onNavigate={navigate}
      />
      <div className="main">
        <TopBar
          theme={theme}
          userName={userName}
          onToggleTheme={toggleTheme}
          demoUrl={demoUrl}
          section={view === "chat" ? (active ? active.title : "Ask anything about Beans") : `Explore Beans · ${PAGE_NAMES[view]}`}
          onOpenMenu={() => setSidebarOpen(true)}
          onClearHistory={clearHistory}
        />
        {view === "chat" ? (
          <main className={`chat-area${showWelcome ? " is-welcome" : ""}`}>
            {showWelcome ? (
              <Welcome onAsk={(q) => ask(q, null)} onNavigate={navigate} onPrefill={prefill} />
            ) : (
              <Chat messages={active.messages} onRetry={retry} onOpenSource={setOpenSource} />
            )}
            <Composer
              inputRef={inputRef}
              value={draft}
              onChange={setDraft}
              onSubmit={() => ask(draft)}
              onStop={() => abortRef.current?.abort()}
              busy={busy}
            />
          </main>
        ) : (
          <Page view={view} onNavigate={navigate} onAsk={(q) => ask(q, null)} />
        )}
      </div>
      <SourceViewer source={openSource} onClose={() => setOpenSource(null)} onAsk={(q) => ask(q, null)} />
    </div>
  );
}

function Page({ view, onNavigate, onAsk }) {
  switch (view) {
    case "explore": return <Explore onNavigate={onNavigate} />;
    case "apis": return <ApiDocs />;
    case "maps": return <Maps />;
    case "integrations": return <Integrations />;
    case "tutorials": return <Tutorials onAsk={onAsk} />;
    case "videos": return <Tutorials onAsk={onAsk} initialTab="videos" />;
    case "articles": return <Tutorials onAsk={onAsk} initialTab="articles" />;
    case "releases": return <ReleaseNotes />;
    default: return <Tutorials onAsk={onAsk} initialTab="articles" />;
  }
}
