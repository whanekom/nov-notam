import { head } from "@vercel/blob";
import { isSignedIn } from "./_auth.js";

const PATH = "mavis/mavis_weather.json";

export default async function handler(req, res) {
  if (!isSignedIn(req)) return res.status(401).json({ error: "Sign in required" });
  try {
    const meta = await head(PATH);
    const r = await fetch(meta.downloadUrl || meta.url, {
      headers: { authorization: `Bearer ${process.env.BLOB_READ_WRITE_TOKEN}` },
      cache: "no-store",
    });
    if (!r.ok) throw new Error(`blob read ${r.status}`);
    res.setHeader("Cache-Control", "private, no-store");
    res.setHeader("Content-Type", "application/json");
    return res.status(200).send(await r.text());
  } catch (e) {
    return res.status(503).json({ error: "No weather stored yet", detail: String(e.message || e) });
  }
}
