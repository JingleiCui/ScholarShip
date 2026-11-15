import katex from 'katex';
import 'katex/dist/katex.min.css';
function entities(text: string): string {
  return text.replace(/&(amp|lt|gt|quot|apos|#\d+|#x[0-9a-f]+);/gi, (all, key: string) => {
    const named: Record<string, string> = { amp: '&', lt: '<', gt: '>', quot: '"', apos: "'" };
    if (key[0] !== '#') return named[key.toLowerCase()] ?? all;
    const n = key[1].toLowerCase() === 'x' ? parseInt(key.slice(2), 16) : parseInt(key.slice(1), 10);
    return n > 0 && n <= 0x10ffff && !(n >= 0xd800 && n <= 0xdfff) ? String.fromCodePoint(n) : '�';
  });
}
function Inline({ text }: { text: string }) {
  const parts = text.split(
    /(```[\s\S]*?```|`[^`\n]+`|\$\$[\s\S]*?\$\$|\$[^\n$]+\$|\\\[[\s\S]*?\\\]|\\\([\s\S]*?\\\))/g,
  );
  return (
    <>
      {parts.map((part, i) => {
        if (part.startsWith('```') && part.endsWith('```') && part.length >= 6)
          return (
            <pre key={i}>
              <code>{part.slice(3, -3)}</code>
            </pre>
          );
        if (part.startsWith('`') && part.endsWith('`')) return <code key={i}>{part.slice(1, -1)}</code>;
        const display = part.startsWith('$$') || part.startsWith('\\[');
        const math =
          (part.startsWith('$') && part.endsWith('$') && part.length > 1) ||
          (part.startsWith('\\[') && part.endsWith('\\]')) ||
          (part.startsWith('\\(') && part.endsWith('\\)'));
        if (math && part.length < 8192) {
          const expression =
            part.startsWith('$$') || part.startsWith('\\[') || part.startsWith('\\(')
              ? part.slice(2, -2)
              : part.slice(1, -1);
          try {
            return (
              <span
                key={i}
                dangerouslySetInnerHTML={{
                  __html: katex.renderToString(expression, {
                    displayMode: display,
                    trust: false,
                    throwOnError: false,
                    maxExpand: 1000,
                    maxSize: 20,
                  }),
                }}
              />
            );
          } catch {
            /* Render unsupported math literally. */
          }
        }
        return <span key={i}>{part}</span>;
      })}
    </>
  );
}
function Table({ text }: { text: string }) {
  const rows = [...text.matchAll(/<tr\b[^>]*>([\s\S]*?)<\/tr>/gi)];
  if (!rows.length || rows.length > 200 || text.length > 50000) return <pre>{text}</pre>;
  function span(attrs: string, name: string) {
    const value = Number(attrs.match(new RegExp(`\\b${name}\\s*=\\s*["']?(\\d+)`, 'i'))?.[1] ?? 1);
    return value >= 1 && value <= 20 ? value : 1;
  }
  return (
    <div style={{ overflowX: 'auto' }}>
      <table style={{ borderCollapse: 'collapse', fontSize: 13 }}>
        <tbody>
          {rows.map((row, i) => (
            <tr key={i}>
              {[...row[1].matchAll(/<(td|th)\b([^>]*)>([\s\S]*?)<\/\1>/gi)].slice(0, 20).map((cell, j) => (
                <td
                  key={j}
                  rowSpan={span(cell[2], 'rowspan')}
                  colSpan={span(cell[2], 'colspan')}
                  style={{ border: '1px solid #b8c8bf', padding: 6 }}
                >
                  <Inline text={entities(cell[3].replace(/<br\s*\/?\s*>/gi, '\n').replace(/<[^>]*>/g, ''))} />
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
export default function ScientificText({ text }: { text: string }) {
  // Only bounded table cells become React elements. No untrusted HTML/attributes enter the DOM.
  return (
    <>
      {text
        .split(/(<table\b[^>]*>[\s\S]*?<\/table>)/gi)
        .map((part, i) =>
          /^<table\b/i.test(part) ? <Table key={i} text={part} /> : <Inline key={i} text={part} />,
        )}
    </>
  );
}
