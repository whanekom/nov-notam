import glob, re
out = []
for f in glob.glob("page_*.html"):
    t = open(f, encoding="utf-8").read()
    i = t.find("Valid from")
    out.append("=== around 'Valid from' ===")
    out.append(t[max(0, i - 600): i + 5200])
    out.append("=== ids in handbook form ===")
    for m in re.finditer(r'<(button|select|input|a)\b[^>]*\bid="(mainForm:handbook[^"]*)"[^>]*>', t):
        tag = m.group(0)
        out.append(re.sub(r'\s+', ' ', tag)[:300])
    out.append("=== result list snippet ===")
    j = t.find("resultList")
    out.append(t[max(0, j - 200): j + 1500])
print("\n".join(out))
