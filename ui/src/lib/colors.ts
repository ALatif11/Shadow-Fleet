// Map layers need RGBA arrays; read them from theme.css so there is one place to tune colours.
export type RGBA = [number, number, number, number];

const cache = new Map<string, [number, number, number]>();

function parse(value: string): [number, number, number] {
  const v = value.trim();
  const hex = v.match(/^#([0-9a-f]{3}|[0-9a-f]{6})$/i);
  if (hex) {
    const h = hex[1].length === 3 ? hex[1].split("").map((c) => c + c).join("") : hex[1];
    return [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16)) as [number, number, number];
  }
  const rgb = v.match(/rgba?\(([^)]+)\)/);
  if (rgb) {
    const [r, g, b] = rgb[1].split(",").map((x) => parseFloat(x));
    return [r, g, b];
  }
  return [255, 255, 255];
}

export function cssRgb(name: string): [number, number, number] {
  let c = cache.get(name);
  if (!c) {
    c = typeof document === "undefined" ? [255, 255, 255] : parse(getComputedStyle(document.documentElement).getPropertyValue(name));
    cache.set(name, c);
  }
  return c;
}

export function rgba(name: string, alpha = 255): RGBA {
  const [r, g, b] = cssRgb(name);
  return [r, g, b, Math.round(alpha)];
}

export const parseColor = parse;
