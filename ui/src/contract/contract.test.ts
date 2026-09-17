// The committed sample bundle (written by Python) must validate against the committed schema, and its
// version must match the generated types. Python checks the same files from its side (tests/test_ui_contract.py).
import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import Ajv2020 from "ajv/dist/2020.js";
import addFormats from "ajv-formats";
import { CONTRACT_VERSION } from "./types.gen";
import type { Dossier, Manifest, Watchlist } from "./index";
import { fileKey, watchlistFile } from "../data/api";

const here = new URL(".", import.meta.url).pathname;
const read = (p: string) => JSON.parse(readFileSync(join(here, p), "utf8"));

const ajv = new Ajv2020({ allErrors: true, strict: false });
addFormats(ajv);
const validators = Object.fromEntries(
  ["manifest", "watchlist", "dossier"].map((n) => [n, ajv.compile(read(`schema/${n}.schema.json`))]),
);

function check(kind: string, data: unknown) {
  const ok = validators[kind](data);
  expect(ok, JSON.stringify(validators[kind].errors?.slice(0, 3))).toBe(true);
}

describe("contract sample", () => {
  const manifest = read("sample/manifest.json") as Manifest;

  it("schemas carry the same version as the generated types", () => {
    for (const n of ["manifest", "watchlist", "dossier"]) {
      expect(read(`schema/${n}.schema.json`)["x-contract-version"]).toBe(CONTRACT_VERSION);
    }
    expect(manifest.contract_version).toBe(CONTRACT_VERSION);
  });

  it("every sample file validates", () => {
    check("manifest", manifest);
    for (const f of readdirSync(join(here, "sample/watchlist"))) check("watchlist", read(`sample/watchlist/${f}`));
    for (const f of readdirSync(join(here, "sample/vessels"))) check("dossier", read(`sample/vessels/${f}`));
  });

  it("file naming matches what the console requests", () => {
    const files = new Set(readdirSync(join(here, "sample/watchlist")).map((f) => `watchlist/${f}`));
    const w = read(`sample/${[...files][0]}`) as Watchlist;
    expect(files.has(watchlistFile(w.cutoff, w.model, w.label_set))).toBe(true);
    for (const id of manifest.vessels) {
      const d = read(`sample/vessels/${fileKey(id)}.json`) as Dossier;
      expect(d.hull_id).toBe(id);
    }
  });

  it("schema rejects a malformed watchlist", () => {
    const w = read(`sample/watchlist/${readdirSync(join(here, "sample/watchlist"))[0]}`);
    w.rows[0].unexpected = 1;
    expect(validators.watchlist(w)).toBe(false);
  });
});
