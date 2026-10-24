import { makeCookie, safeEqual } from "./_auth.js";

export default async function handler(req, res) {
  if (req.method !== "POST") return res.status(405).end();
  const body = typeof req.body === "string" ? JSON.parse(req.body || "{}") : req.body || {};
  const userOk = safeEqual(String(body.username || "").trim().toUpperCase(), String(process.env.SITE_USER || "").toUpperCase());
  const passOk = safeEqual(body.password, process.env.SITE_PASSWORD);
  if (!(userOk && passOk)) {
    return res.status(401).json({ error: "Wrong username or password" });
  }
  res.setHeader("Set-Cookie", makeCookie());
  return res.status(200).json({ ok: true });
}
