import { head, put } from "@vercel/blob";
import { isSignedIn } from "./_auth.js";

// Lets a signed-in user kick off an out-of-cycle MAVIS pull instead of
// waiting for the next GitHub Actions run. We don't scrape here (that needs
// Playwright/Chromium, which doesn't fit a Vercel function) — we just ask
// GitHub to dispatch the existing workflow, which then POSTs to /api/upload
// as usual once it finishes (typically ~60-90s later).

const REPO = "whanekom/nov-notam";
const WORKFLOW = "mavis-weather.yml";
const LOCK_PATH = "mavis/last-refresh.json";
const MIN_GAP_MS = 90_000; // don't let the button spam GitHub / MAVIS

export default async function handler(req, res) {
  if (!isSignedIn(req)) return res.status(401).json({ error: "Sign in required" });
  if (req.method !== "POST") return res.status(405).end();

  const token = process.env.GH_DISPATCH_TOKEN;
  if (!token) return res.status(500).json({ error: "Refresh isn't configured (missing GH_DISPATCH_TOKEN)" });

  // Simple shared rate limit so two tabs / an itchy trigger finger can't
  // fire off a pile of workflow runs.
  try {
    const meta = await head(LOCK_PATH).catch(() => null);
    if (meta) {
      const r = await fetch(meta.downloadUrl || meta.url, {
        headers: { authorization: `Bearer ${process.env.BLOB_READ_WRITE_TOKEN}` },
        cache: "no-store",
      });
      if (r.ok) {
        const { at } = await r.json();
        const waitMs = MIN_GAP_MS - (Date.now() - at);
        if (waitMs > 0) {
          return res.status(429).json({ error: "Refresh already requested recently", retryInMs: waitMs });
        }
      }
    }
  } catch {
    // If the lock check fails for any reason, don't let that block a refresh.
  }

  const ghRes = await fetch(`https://api.github.com/repos/${REPO}/actions/workflows/${WORKFLOW}/dispatches`, {
    method: "POST",
    headers: {
      authorization: `Bearer ${token}`,
      accept: "application/vnd.github+json",
      "content-type": "application/json",
    },
    body: JSON.stringify({ ref: "main" }),
  });

  if (ghRes.status !== 204) {
    const detail = await ghRes.text().catch(() => "");
    return res.status(502).json({ error: `GitHub dispatch failed (${ghRes.status})`, detail });
  }

  await put(LOCK_PATH, JSON.stringify({ at: Date.now() }), {
    access: "private",
    allowOverwrite: true,
    addRandomSuffix: false,
    contentType: "application/json",
  });

  return res.status(200).json({ ok: true, message: "Refresh requested — usually ready in a minute or two" });
}
