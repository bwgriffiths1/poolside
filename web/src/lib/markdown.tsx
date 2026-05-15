import { type ReactNode } from "react";

// Very small markdown → JSX renderer for agenda-item summary bodies.
// Handles: paragraphs, h3 (### ), h4 (#### ), bullets (- / *), inline **bold**
// and *italic*. Not a full markdown parser — just enough to render the
// summarizer's output without literal asterisks leaking through.

export function inlineMd(text: string): ReactNode[] {
  return inline(text);
}

// Common markdown escapes the LLM emits to keep literal punctuation intact
// (e.g. `\$9,337/MWh` so it isn't read as LaTeX). Strip the backslash before
// returning the unescaped char.
const ESC_CHARS = new Set("$_*`[](){}#.-+!|<>%~&");

function unescape(s: string): string {
  let out = "";
  let i = 0;
  while (i < s.length) {
    if (s[i] === "\\" && i + 1 < s.length && ESC_CHARS.has(s[i + 1])) {
      out += s[i + 1];
      i += 2;
    } else {
      out += s[i];
      i += 1;
    }
  }
  return out;
}

function inline(text: string): ReactNode[] {
  const nodes: ReactNode[] = [];
  let i = 0;
  // Order matters: image (`![..](..)`) must be checked before link (`[..](..)`).
  const re =
    /(!\[([^\]]*)\]\(([^)\s]+)\)|\[([^\]]+)\]\(([^)\s]+)\)|\*\*([^*]+)\*\*|\*([^*]+)\*|`([^`]+)`)/g;
  let m: RegExpExecArray | null;
  let key = 0;
  while ((m = re.exec(text))) {
    if (m.index > i) nodes.push(unescape(text.slice(i, m.index)));
    if (m[3] !== undefined) {
      // image: m[2]=alt, m[3]=src
      nodes.push(
        <img
          key={key++}
          src={m[3]}
          alt={unescape(m[2] || "")}
          className="md-img"
          loading="lazy"
        />
      );
    } else if (m[5] !== undefined) {
      // link: m[4]=text, m[5]=href
      const href = m[5];
      const internal = href.startsWith("/") || href.startsWith("#");
      nodes.push(
        <a
          key={key++}
          href={href}
          target={internal ? undefined : "_blank"}
          rel={internal ? undefined : "noopener noreferrer"}
        >
          {unescape(m[4])}
        </a>
      );
    } else if (m[6] !== undefined) {
      nodes.push(<strong key={key++}>{unescape(m[6])}</strong>);
    } else if (m[7] !== undefined) {
      nodes.push(<em key={key++}>{unescape(m[7])}</em>);
    } else if (m[8] !== undefined) {
      nodes.push(<code key={key++}>{unescape(m[8])}</code>);
    }
    i = m.index + m[0].length;
  }
  if (i < text.length) nodes.push(unescape(text.slice(i)));
  return nodes;
}

interface MarkdownProps {
  source: string;
  className?: string;
}

export function Markdown({ source, className }: MarkdownProps) {
  const lines = source.split("\n");
  const blocks: ReactNode[] = [];
  let i = 0;
  let key = 0;

  while (i < lines.length) {
    const line = lines[i];
    const trimmed = line.trim();

    if (!trimmed) {
      i += 1;
      continue;
    }

    if (trimmed.startsWith("#### ")) {
      blocks.push(<h4 key={key++}>{inline(trimmed.slice(5))}</h4>);
      i += 1;
      continue;
    }
    if (trimmed.startsWith("### ")) {
      blocks.push(<h3 key={key++}>{inline(trimmed.slice(4))}</h3>);
      i += 1;
      continue;
    }
    if (trimmed.startsWith("## ")) {
      blocks.push(<h3 key={key++}>{inline(trimmed.slice(3))}</h3>);
      i += 1;
      continue;
    }

    if (trimmed.startsWith("- ") || trimmed.startsWith("* ")) {
      const items: ReactNode[] = [];
      while (i < lines.length) {
        const ln = lines[i].trim();
        if (!ln.startsWith("- ") && !ln.startsWith("* ")) break;
        items.push(<li key={items.length}>{inline(ln.slice(2))}</li>);
        i += 1;
      }
      blocks.push(<ul key={key++}>{items}</ul>);
      continue;
    }

    if (trimmed.startsWith("---")) {
      blocks.push(<hr key={key++} />);
      i += 1;
      continue;
    }

    // Callout / admonition: `> [!Label] body…` (and any continuation blockquote lines)
    const calloutMatch = trimmed.match(/^>\s*\[!([^\]]+)\]\s*(.*)$/);
    if (calloutMatch) {
      const label = calloutMatch[1].trim();
      const bodyParts: string[] = [];
      if (calloutMatch[2]) bodyParts.push(calloutMatch[2]);
      i += 1;
      while (i < lines.length) {
        const ln = lines[i];
        if (!ln.trim().startsWith(">")) break;
        const rest = ln.replace(/^\s*>\s?/, "");
        bodyParts.push(rest);
        i += 1;
      }
      blocks.push(
        <div key={key++} className="md-callout" data-label={label}>
          <div className="md-callout-label">{label}</div>
          <div className="md-callout-body">{inline(bodyParts.join(" "))}</div>
        </div>
      );
      continue;
    }

    // Pipe table: collect contiguous `| ... |` rows, drop the `|---|---` separator.
    if (trimmed.startsWith("|") && trimmed.endsWith("|")) {
      const rows: string[][] = [];
      while (i < lines.length) {
        const ln = lines[i].trim();
        if (!ln.startsWith("|") || !ln.endsWith("|")) break;
        const cells = ln.slice(1, -1).split("|").map((c) => c.trim());
        if (!cells.every((c) => /^:?-{2,}:?$/.test(c))) {
          rows.push(cells);
        }
        i += 1;
      }
      if (rows.length >= 1) {
        const [head, ...body] = rows;
        blocks.push(
          <table key={key++} className="md-table">
            <thead>
              <tr>
                {head.map((c, ci) => (
                  <th key={ci}>{inline(c)}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {body.map((row, ri) => (
                <tr key={ri}>
                  {row.map((c, ci) => (
                    <td key={ci}>{inline(c)}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        );
      }
      continue;
    }

    // Paragraph — collect contiguous non-blank, non-special lines.
    const para: string[] = [line];
    i += 1;
    while (i < lines.length) {
      const nxt = lines[i];
      if (!nxt.trim()) break;
      const tt = nxt.trim();
      if (
        tt.startsWith("#") ||
        tt.startsWith("- ") ||
        tt.startsWith("* ") ||
        tt.startsWith("---") ||
        (tt.startsWith("|") && tt.endsWith("|"))
      )
        break;
      para.push(nxt);
      i += 1;
    }
    blocks.push(<p key={key++}>{inline(para.join(" "))}</p>);
  }

  return <div className={className}>{blocks}</div>;
}
