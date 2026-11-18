import type { CSSProperties } from 'react';
export const BRAND = {
  name: 'ScholarShip',
  chinese: '学者之舟',
  slogan: '学海无涯，AI作舟',
  mark: '/brand/scholarship-mark.svg',
};
export function BrandMark({ size = 40, style }: { size?: number; style?: CSSProperties }) {
  return <img src={BRAND.mark} width={size} height={size} alt="" aria-hidden="true" style={style} />;
}
export default function Brand() {
  return (
    <>
      <BrandMark />
      <span className="brand-word">
        {BRAND.name}
        <small>{BRAND.chinese}</small>
      </span>
    </>
  );
}
export function Icon({ name, size = 22 }: { name: string; size?: number }) {
  const paths: Record<string, string> = {
    arrow: 'M5 12h14m-6-6 6 6-6 6',
    book: 'M12 5v15M12 5C8 2 3 4 3 4v14s5-2 9 2c4-4 9-2 9-2V4s-5-2-9 1Z',
    file: 'M14 3H5v18h14V8l-5-5Zm0 0v5h5M8 12h8m-8 4h6',
    source: 'M9 5H5v14h4m6-14h4v14h-4M9 12h6',
    search: 'm16 16 5 5M18 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0Z',
    shield: 'M12 3 4 6v6c0 5 8 9 8 9s8-4 8-9V6l-8-3Zm-4 9 3 3 5-6',
    chart: 'M4 3v17h17M8 15v-4m5 4V6m5 9v-7',
    graph:
      'M7 7l10 1M6 9l5 9m6-8-4 8M8 5a3 3 0 1 1-6 0 3 3 0 0 1 6 0Zm14 3a3 3 0 1 1-6 0 3 3 0 0 1 6 0Zm-7 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0Z',
    pen: 'm15 4 5 5M4 20l5-1L21 7l-5-5L4 14v6Z',
    layers: 'm3 7 9-5 9 5-9 5-9-5Zm0 5 9 5 9-5M3 17l9 5 9-5',
    check: 'm5 12 4 4L19 6',
    clock: 'M12 7v5l3 2M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0Z',
  };
  return (
    <svg
      className="ui-icon"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d={paths[name] ?? paths.file} />
    </svg>
  );
}
