import { describe, it, expect } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import ScientificText from './ScientificText';
describe('source-bound scientific rendering', () => {
  it('renders formulas and merged table headers without accepting HTML attributes', () => {
    const html = renderToStaticMarkup(
      <ScientificText
        text={
          'Table 2\n<table><tr><td colspan="2">BLEU</td></tr><tr><td>EN-DE</td><td>$x^2$ &amp; value</td></tr></table>'
        }
      />,
    );
    expect(html).toContain('colSpan="2"');
    expect(html).toContain('katex');
    expect(html).toContain('EN-DE');
    expect(html).toContain('&amp; value');
  });
  it('never inserts untrusted HTML, external images or handlers', () => {
    const html = renderToStaticMarkup(
      <ScientificText
        text={
          '<img src="https://evil.example/image" onerror="alert(1)"><table><tr><td onclick="alert(1)">&lt;img src=x&gt;<script>alert(1)</script></td></tr></table>'
        }
      />,
    );
    expect(html).not.toContain('<img');
    expect(html).not.toContain('<script');
    expect(html).not.toMatch(/<td[^>]*onclick=/);
  });
  it('keeps code and incomplete math literal instead of reinterpreting them', () => {
    const html = renderToStaticMarkup(<ScientificText text={'`$x^2$` and $unfinished'} />);
    expect(html).toContain('<code>$x^2$</code>');
    expect(html).toContain('$unfinished');
    expect(html).not.toContain('class="katex"');
  });
});
