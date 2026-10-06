// The design system is only a system if it is enforced. These tests fail the build when a colour,
// duration or easing curve is written outside theme.css, when contrast drops below WCAG AA, or when
// one of the banned surface effects comes back.
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const SRC = new URL(".", import.meta.url).pathname;
const theme = readFileSync(join(SRC, "theme.css"), "utf8");
const app = readFileSync(join(SRC, "app.css"), "utf8");

function walk(dir: string): string[] {
  return readdirSync(dir).flatMap((f) => {
    const p = join(dir, f);
    return statSync(p).isDirectory() ? (f === "contract" ? [] : walk(p)) : p.endsWith(".tsx") ? [p] : [];
  });
}
const components = walk(SRC).map((p) => [p.replace(SRC, ""), readFileSync(p, "utf8")] as const);

// ---------------------------------------------------------------- tokens
const tokens = Object.fromEntries([...theme.matchAll(/(--[\w-]+):\s*([^;]+);/g)].map((m) => [m[1], m[2].trim()]));

function resolve(name: string, depth = 0): string {
  const v = tokens[name];
  const alias = v?.match(/^var\(\s*(--[\w-]+)\s*\)$/);
  return alias && depth < 4 ? resolve(alias[1], depth + 1) : v;
}
function rgb(hex: string): [number, number, number] {
  const h = hex.replace("#", "");
  const f = h.length === 3 ? h.split("").map((c) => c + c).join("") : h;
  return [0, 2, 4].map((i) => parseInt(f.slice(i, i + 2), 16)) as [number, number, number];
}
function luminance(hex: string) {
  const [r, g, b] = rgb(hex).map((v) => {
    const c = v / 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}
const contrast = (a: string, b: string) => {
  const [x, y] = [luminance(a), luminance(b)].sort((p, q) => q - p);
  return (x + 0.05) / (y + 0.05);
};

describe("colour", () => {
  const cases: [string, string, number][] = [
    ["--ink", "--paper", 4.5],
    ["--ink", "--card", 4.5],
    ["--ink", "--surface-2", 4.5],
    ["--muted", "--card", 4.5],
    ["--accent-700", "--card", 4.5],
    ["--accent-500", "--card", 4.5],
    ["--danger", "--card", 4.5],
    ["--hindsight", "--card", 4.5],
    ["--faint", "--card", 3],
    ["--control-border", "--card", 3],
    ["--line", "--card", 1.2],
  ];
  it.each(cases)("%s on %s", (fg, bg, min) => {
    expect(contrast(resolve(fg), resolve(bg))).toBeGreaterThanOrEqual(min);
  });

  it("every event colour stays legible on the map and is distinct", () => {
    const evs = Object.keys(tokens).filter((t) => t.startsWith("--ev-"));
    expect(evs.length).toBe(9);
    for (const e of evs) expect(contrast(resolve(e), resolve("--map-bg")), e).toBeGreaterThanOrEqual(3);
    for (const e of evs) {
      expect(resolve(e), `${e} must not reuse the destructive red`).not.toBe(resolve("--danger"));
    }
    expect(new Set(evs.map((e) => resolve(e))).size).toBe(evs.length);
  });

  it("app.css declares no literal colours: every colour is a token", () => {
    const stripped = app.replace(/\/\*[\s\S]*?\*\//g, "");
    expect(stripped.match(/#[0-9a-f]{3,8}\b/gi) ?? []).toEqual([]);
    expect(stripped.match(/\brgba?\([^)]*\)/g) ?? []).toEqual([]);
  });
});

describe("motion", () => {
  it("durations and easing curves exist only in theme.css", () => {
    const sources: [string, string][] = [["app.css", app], ...components.map(([n, c]) => [n, c] as [string, string])];
    for (const [name, text] of sources) {
      const stripped = text.replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/[^\n]*/g, "");
      const durations = [...stripped.matchAll(/(transition|animation)[^;{]*?:\s*([^;]+);/g)]
        .flatMap((m) => m[2].match(/\b\d*\.?\d+m?s\b/g) ?? [])
        .filter((d) => !/^0\.001ms$/.test(d));
      expect(durations, `${name} hand-writes a duration`).toEqual([]);
      expect(stripped.match(/cubic-bezier|ease-in-out|\bease-in\b|linear\)/g) ?? [], `${name} hand-writes an easing curve`).toEqual([]);
    }
  });

  it("keyframes live only in theme.css, and every one of them is used", () => {
    expect(app.match(/@keyframes/g) ?? []).toEqual([]);
    const names = [...theme.matchAll(/@keyframes ([\w-]+)/g)].map((m) => m[1]);
    expect(names).toEqual(expect.arrayContaining(["view-in", "bar-in"]));
    for (const n of names) expect(app.includes(n) || components.some(([, c]) => c.includes(n)), `${n} is unused`).toBe(true);
    expect(components.filter(([, c]) => c.includes("view-in")).length).toBeGreaterThanOrEqual(3);
  });

  it("reduced motion kills every transition and animation", () => {
    expect(theme).toMatch(/prefers-reduced-motion: reduce/);
    expect(theme).toMatch(/animation-duration: 0\.001ms !important/);
    expect(theme).toMatch(/transition-duration: 0\.001ms !important/);
  });

  it("two tiers: interactions stay under 0.25s, the big moments under 0.4s", () => {
    for (const t of ["--t-fast", "--t-view"]) expect(parseFloat(resolve(t)), t).toBeLessThanOrEqual(0.25);
    for (const t of ["--t-stage", "--t-count", "--t-decode"]) expect(parseFloat(resolve(t)), t).toBeLessThanOrEqual(0.4);
    // Every transition in app.css runs on one of those tokens.
    const used = [...app.matchAll(/(?:transition|animation)[^;{]*?:\s*([^;]+);/g)].flatMap((m) => m[1].match(/--t-[\w-]+/g) ?? []);
    const allowed = ["--t-fast", "--t-view", "--t-stage", "--t-stagger", "--t-breathe", "--t-breathe-slow", "--t-spin"];
    for (const u of used) expect(allowed, u).toContain(u);
  });

  it("JavaScript motion goes through lib/motion.ts, which honours reduced motion", () => {
    for (const [name, c] of components) {
      expect(c, `${name} animates on its own`).not.toMatch(/requestAnimationFrame|\.animate\(/);
    }
    const motion = readFileSync(join(SRC, "lib/motion.ts"), "utf8");
    expect(motion).toMatch(/prefers-reduced-motion: reduce/);
  });

  it("no bouncy easing: every curve stays inside 0..1", () => {
    for (const [, curve] of theme.matchAll(/cubic-bezier\(([^)]+)\)/g)) {
      const [, y1, , y2] = curve.split(",").map(Number);
      expect(y1).toBeGreaterThanOrEqual(0);
      expect(y2).toBeLessThanOrEqual(1);
    }
  });
});

describe("interaction", () => {
  it("presses sink and focus rings are never removed", () => {
    expect(app).toMatch(/:active[^{]*\{[^}]*transform: scale\(0\.98\)/);
    expect(app).toMatch(/:focus-visible\s*\{[\s\S]*?outline: var\(--focus-width\) solid var\(--accent-500\)/);
    expect(app).not.toMatch(/outline:\s*(none|0)\s*;(?![^]*:focus:not)/);
  });

  it("touch targets reach 44px", () => {
    expect(app).toMatch(/@media \(pointer: coarse\)[\s\S]*?min-height: 44px/);
  });

});

describe("surfaces", () => {
  it("no glassmorphism, no surface gradients, no resting shadow on cards", () => {
    expect(app).not.toMatch(/backdrop-filter/);
    // Gradients are allowed only inside the .fx- degradation overlays (brutalist-skill section 7).
    const surfaces = app.replace(/\n\.fx\b[\s\S]*?\n\}/g, "");
    expect(surfaces).not.toMatch(/gradient\(/);
    const card = app.match(/\n\.panel \{[\s\S]*?\}/)?.[0] ?? "";
    expect(card).not.toMatch(/box-shadow/);
    expect(card).toMatch(/border-radius: var\(--r-container\)/);
  });

  it("elevation is reserved for floating surfaces and primary actions", () => {
    const shadows = [...app.matchAll(/box-shadow:\s*([^;]+);/g)].map((m) => m[1].trim());
    for (const s of shadows) expect(s).toMatch(/var\(--lift-accent\)|inset/);
  });
});
