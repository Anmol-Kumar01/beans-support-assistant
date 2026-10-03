// A small, safe markdown subset for bot answers: paragraphs, nested lists, headings, bold,
// italic, code, http(s) links, and [n] citation chips. Text is escaped before tags are added.

function esc(s) {
  return s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

function inline(text, sources) {
  const codes = [];
  let s = esc(text).replace(/`([^`\n]+)`/g, (_, c) => `\u0000${codes.push(c) - 1}\u0000`);
  s = s.replace(/\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\)/g,
    (_, t, u) => `<a href="${u}" target="_blank" rel="noopener noreferrer">${t}</a>`);
  s = s.replace(/\[(\d{1,3})\]/g, (m, n) => {
    if (!sources) return `<span class="cite">${n}</span>`; // still streaming
    const src = sources.get(Number(n));
    return src ? `<button type="button" class="cite" data-n="${n}" title="${esc(src.title || "")}">${n}</button>` : m;
  });
  s = s.replace(/\*\*([^*\n]+?)\*\*/g, "<strong>$1</strong>");
  s = s.replace(/(^|[^*\w])\*([^*\n]+?)\*(?![*\w])/g, "$1<em>$2</em>");
  return s.replace(/\u0000(\d+)\u0000/g, (_, i) => `<code>${codes[i]}</code>`);
}

export function renderMarkdown(md, sources) {
  let out = "";
  let para = [];
  const lists = [];
  const flushPara = () => {
    if (para.length) out += `<p>${inline(para.join(" "), sources)}</p>`;
    para = [];
  };
  const closeLists = (above = -1) => {
    while (lists.length && lists.at(-1).indent > above) out += `</li></${lists.pop().type}>`;
  };
  for (const raw of md.replace(/\r\n?/g, "\n").split("\n")) {
    const item = raw.match(/^(\s*)([-*+]|(\d+)[.)])\s+(.*)$/);
    const heading = raw.match(/^\s*(#{1,4})\s+(.*)$/);
    if (heading) {
      flushPara();
      closeLists();
      const level = Math.min(heading[1].length + 2, 5);
      out += `<h${level}>${inline(heading[2], sources)}</h${level}>`;
    } else if (item) {
      flushPara();
      const indent = item[1].replace(/\t/g, "    ").length;
      const type = item[3] ? "ol" : "ul";
      closeLists(indent);
      const top = lists.at(-1);
      if (top && top.indent === indent && top.type === type) {
        out += "</li><li>";
      } else {
        if (top && top.indent === indent) out += `</li></${lists.pop().type}>`;
        const start = Number(item[3] || 1);
        out += `<${type}${type === "ol" && start > 1 ? ` start="${start}"` : ""}><li>`;
        lists.push({ type, indent });
      }
      out += inline(item[4], sources);
    } else if (!raw.trim()) {
      flushPara(); // blank lines keep lists open so numbering continues
    } else if (lists.length && /^\s/.test(raw)) {
      out += " " + inline(raw.trim(), sources);
    } else {
      closeLists();
      para.push(raw.trim());
    }
  }
  flushPara();
  closeLists();
  return out;
}
