import type { BriefingBlock } from "../../types";
import { inlineMd } from "../../lib/markdown";

function isDelta(s: string): boolean {
  return /^[-+]/.test(s);
}

export function BlockRenderer({ block }: { block: BriefingBlock }) {
  switch (block.kind) {
    case "p":
      // Bullet paragraphs come back as multi-line with "• " prefix; render with line breaks.
      if (block.text.includes("\n")) {
        return (
          <p className="b-p" style={{ whiteSpace: "pre-line" }}>
            {inlineMd(block.text)}
          </p>
        );
      }
      return <p className="b-p">{inlineMd(block.text)}</p>;
    case "h":
      return <h3 className="b-h3">{inlineMd(block.text)}</h3>;
    case "callout":
      return (
        <div className="b-callout">
          <div className="b-callout-label">{block.label}</div>
          <div className="b-callout-body">{inlineMd(block.text)}</div>
        </div>
      );
    case "data": {
      const [header, ...body] = block.rows;
      return (
        <figure className="b-figure">
          <table className="b-table">
            <thead>
              <tr>
                {header.map((c, i) => (
                  <th key={i}>{inlineMd(c)}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {body.map((row, ri) => (
                <tr key={ri}>
                  {row.map((cell, ci) => {
                    const isLast = ci === row.length - 1;
                    const isFirst = ci === 0;
                    if (isLast && isDelta(cell)) {
                      return (
                        <td key={ci} className="mono num">
                          <span
                            className={
                              cell.startsWith("+") ? "delta-pos" : "delta-neg"
                            }
                          >
                            {inlineMd(cell)}
                          </span>
                        </td>
                      );
                    }
                    return (
                      <td key={ci} className={isFirst ? "" : "mono num"}>
                        {inlineMd(cell)}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
          <figcaption>{block.title}</figcaption>
        </figure>
      );
    }
    default:
      return null;
  }
}
