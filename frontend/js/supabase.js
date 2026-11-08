// One shared Supabase client for every page (multiple clients cause auth warnings).
// config.js is user-created (copy config.example.js). If it's missing we fail softly so
// pages can show a helpful message instead of a blank screen.
let cfg = {};
try {
  cfg = await import("./config.js");
} catch {
  /* handled via configOk below */
}

export const configOk =
  Boolean(cfg.SUPABASE_KEY) &&
  !String(cfg.SUPABASE_KEY).startsWith("YOUR_") &&
  Boolean(cfg.SUPABASE_URL) &&
  typeof window !== "undefined" &&
  Boolean(window.supabase);

export const sb = configOk ? window.supabase.createClient(cfg.SUPABASE_URL, cfg.SUPABASE_KEY) : null;

// Where the local Python API runs (default: your own computer).
export const API_URL = (cfg.API_URL || "http://localhost:8080").replace(/\/$/, "");
