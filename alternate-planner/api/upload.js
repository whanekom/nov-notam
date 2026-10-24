import { put, head } from "@vercel/blob";
import { safeEqual } from "./_auth.js";

const PATH = "mavis/mavis_weather.json";

export const config = { api: { bodyParser: { sizeLimit: "4mb" } } };

export default async function handler(req, res) {
  const key = (req.headers.authorization || "").replace(/^Bearer\s+/i, "");
  if (!safeEqual(key, process.env.UPLOAD_KEY)) return res.status(401).json({ error: "Bad key" });
  if (req.method === "GET") {
    // The collector reads the stored file back so it can extend the 24-hour history.
    try {
      const meta = await head(PATH);
      const r = await fetch(meta.downloadUrl || meta.url, {
        headers: { authorization: `Bearer ${process.env.BLOB_READ_WRITE_TOKEN}` }, cache: "no-store" });
      if (!r.ok) throw new Error(String(r.status));
      res.setHeader("Content-Type", "application/json");
      return res.status(200).send(await r.text());
    } catch { return res.status(404).json({ error: "Nothing stored yet" }); }
  }
  if (req.method !== "POST") return res.status(405).end();
  const text = typeof req.body === "string" ? req.body : JSON.stringify(req.body);
  let parsed;
  try { parsed = JSON.parse(text); } catch { return res.status(400).json({ error: "Not JSON" }); }
  if (!parsed.latest || !parsed.history) return res.status(400).json({ error: "Unexpected shape" });
  await put(PATH, text, {
    access: "private",
    allowOverwrite: true,
    addRandomSuffix: false,
    contentType: "application/json",
    cacheControlMaxAge: 60,
  });
  return res.status(200).json({ ok: true, stations: Object.keys(parsed.latest).length, snapshots: parsed.history.length });
}
