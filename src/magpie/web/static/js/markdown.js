// A small, safe Markdown renderer for Claude's reviews, answers and digests.
// It builds DOM nodes directly (no innerHTML) and supports just what Claude
// writes: headings, lists, quotes, paragraphs, bold, italics, code, links, and
// note references like (#12).

const INLINE =
  /(\*\*([^*]+)\*\*)|(`([^`]+)`)|(\[([^\]]+)\]\((https?:\/\/[^)\s]+)\))|(https?:\/\/[^\s<>()]+[^\s<>().,;:!?'"])|(\(#(\d+)\))|(\*([^*\s][^*]*?)\*)|(_([^_\s][^_]*?)_)/g;

export function inline(text) {
  const fragment = document.createDocumentFragment();
  let last = 0;
  for (const match of String(text).matchAll(INLINE)) {
    if (match.index > last) fragment.append(text.slice(last, match.index));
    if (match[1]) fragment.append(el("strong", match[2]));
    else if (match[3]) fragment.append(el("code", match[4]));
    else if (match[5]) fragment.append(link(match[6], match[7]));
    else if (match[8]) fragment.append(link(match[8], match[8]));
    else if (match[9]) {
      const a = document.createElement("a");
      a.href = `#/note/${match[10]}`;
      a.textContent = `(#${match[10]})`;
      fragment.append(a);
    } else if (match[11]) fragment.append(el("em", match[12]));
    else if (match[13]) fragment.append(el("em", match[14]));
    last = match.index + match[0].length;
  }
  if (last < text.length) fragment.append(text.slice(last));
  return fragment;
}

function el(tag, text) {
  const node = document.createElement(tag);
  node.textContent = text;
  return node;
}

function link(text, href) {
  const a = document.createElement("a");
  a.href = href;
  a.textContent = text;
  a.target = "_blank";
  a.rel = "noopener noreferrer";
  return a;
}

export function renderMarkdown(source) {
  const root = document.createElement("div");
  root.className = "markdown";
  root.dir = "auto";
  const lines = String(source || "").replace(/\r\n/g, "\n").split("\n");
  let list = null;
  let paragraph = [];

  const flushParagraph = () => {
    if (paragraph.length) {
      const p = document.createElement("p");
      paragraph.forEach((line, i) => {
        if (i) p.append(document.createElement("br"));
        p.append(inline(line));
      });
      root.append(p);
      paragraph = [];
    }
  };
  const flushList = () => {
    list = null;
  };

  for (const raw of lines) {
    const line = raw.trimEnd();
    const heading = line.match(/^(#{1,4})\s+(.*)$/);
    const bullet = line.match(/^\s*[-*•]\s+(.*)$/);
    const numbered = line.match(/^\s*\d+[.)]\s+(.*)$/);
    const quote = line.match(/^>\s?(.*)$/);
    if (!line.trim()) {
      flushParagraph();
      flushList();
    } else if (heading) {
      flushParagraph();
      flushList();
      const level = Math.min(heading[1].length + 1, 4);
      const node = document.createElement(`h${level}`);
      node.append(inline(heading[2]));
      root.append(node);
    } else if (bullet || numbered) {
      flushParagraph();
      const tag = bullet ? "ul" : "ol";
      if (!list || list.tagName.toLowerCase() !== tag) {
        list = document.createElement(tag);
        root.append(list);
      }
      const item = document.createElement("li");
      item.append(inline((bullet || numbered)[1]));
      list.append(item);
    } else if (quote) {
      flushParagraph();
      flushList();
      const node = document.createElement("blockquote");
      node.append(inline(quote[1]));
      root.append(node);
    } else {
      flushList();
      paragraph.push(line);
    }
  }
  flushParagraph();
  return root;
}
