import crypto from "node:crypto";

const COOKIE = "wf_session";
const DAYS = 30;

function sign(value) {
  return crypto.createHmac("sha256", process.env.SESSION_SECRET || "").update(value).digest("hex");
}

export function makeCookie() {
  const exp = Date.now() + DAYS * 864e5;
  const token = `${exp}.${sign(String(exp))}`;
  return `${COOKIE}=${token}; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age=${DAYS * 86400}`;
}

export function isSignedIn(req) {
  const raw = (req.headers.cookie || "").split(/;\s*/).find((c) => c.startsWith(COOKIE + "="));
  if (!raw) return false;
  const [exp, sig] = raw.slice(COOKIE.length + 1).split(".");
  if (!exp || !sig || Number(exp) < Date.now()) return false;
  const good = sign(exp);
  return sig.length === good.length && crypto.timingSafeEqual(Buffer.from(sig), Buffer.from(good));
}

export function safeEqual(a, b) {
  const x = Buffer.from(String(a || "")), y = Buffer.from(String(b || ""));
  return x.length === y.length && x.length > 0 && crypto.timingSafeEqual(x, y);
}
