// Recent chats, kept in this browser only (localStorage) until conversations are stored server-side.

const KEY = "beans.recentChats";
const MAX_CHATS = 30;

export function loadChats() {
  try {
    return JSON.parse(localStorage.getItem(KEY)) || [];
  } catch {
    return [];
  }
}

export function saveChats(chats) {
  try {
    localStorage.setItem(KEY, JSON.stringify(chats.slice(0, MAX_CHATS)));
  } catch {
    /* storage full or blocked: history just isn't kept */
  }
}

export function timeAgo(ts) {
  const s = Math.max(0, (Date.now() - ts) / 1000);
  if (s < 60) return "just now";
  const m = Math.floor(s / 60);
  if (m < 60) return `${m} min ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h} hour${h > 1 ? "s" : ""} ago`;
  const d = Math.floor(h / 24);
  return `${d} day${d > 1 ? "s" : ""} ago`;
}

export function clockTime(ts) {
  return new Date(ts).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

export function newId() {
  return crypto.randomUUID ? crypto.randomUUID() : `c-${Date.now().toString(36)}${Math.random().toString(36).slice(2)}`;
}
