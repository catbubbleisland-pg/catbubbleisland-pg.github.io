#!/usr/bin/env python3
"""Construye el sitio publico de Cat Bubble Island a partir de Web/site/ y Web/site-config.json.

    python3 Web/build.py --out Web/dist                 # modo estricto (publicable)
    python3 Web/build.py --out Web/dist --preview       # vista previa local, admite pendientes
    python3 Web/build.py --config otro.json --out DIR   # config alternativa (pruebas)

Modo estricto FALLA (exit 1) si: falta un valor, un valor no esta marcado verified=true,
un control de release esta en false, queda algun marcador {{...}} o bloque condicional,
un enlace interno no resuelve, una imagen no coincide con sus atributos width/height,
o falta alt/lang/title/canonical. Solo usa la biblioteca estandar (corre en GitHub Actions).
"""
import argparse, datetime, html.parser, json, os, re, shutil, struct, sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "site")
MARKER = re.compile(r"\{\{([A-Z0-9_]+)\}\}")
MONTHS_ES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
             "septiembre", "octubre", "noviembre", "diciembre"]
TEXT_EXT = (".html", ".css", ".txt", ".xml", ".json")

def fail(errors):
    print("\nBUILD FALLIDO:", file=sys.stderr)
    for e in errors:
        print("  - " + e, file=sys.stderr)
    sys.exit(1)

def load_config(path, preview):
    cfg = json.load(open(path, encoding="utf-8"))
    errors, values = [], {}
    for key, spec in cfg["values"].items():
        v = str(spec.get("value", "")).strip()
        if not v:
            errors.append(f"{key}: sin valor (candidato: {spec.get('candidate', '-')})")
        elif not spec.get("verified"):
            errors.append(f"{key}: valor '{v}' sin verificar (verified=false)")
        values[key] = v
    for key, ok in cfg.get("release_checks", {}).items():
        if key.startswith("_"):
            continue
        if ok is not True:
            errors.append(f"release_checks.{key} no esta confirmado (true)")
    opt = cfg.get("optional", {})
    # Validaciones de formato
    su = values.get("SITE_URL", "")
    if su and not re.match(r"^https://[a-z0-9.-]+(/[A-Za-z0-9._-]+)*$", su):
        errors.append("SITE_URL debe ser https://host[/ruta] sin barra final")
    for k in ("SUPPORT_EMAIL", "PRIVACY_EMAIL"):
        if values.get(k) and not re.match(r"^[^@\s]+@[^@\s]+\.[a-z]{2,}$", values[k]):
            errors.append(f"{k} no parece un correo valido")
    ed = values.get("EFFECTIVE_DATE", "")
    try:
        d = datetime.date.fromisoformat(ed) if ed else None
    except ValueError:
        d = None
        errors.append("EFFECTIVE_DATE debe ser AAAA-MM-DD")
    for k in [k for k in values if k.endswith("_DAYS")]:
        if values[k] and not values[k].isdigit():
            errors.append(f"{k} debe ser un numero entero de dias")
    if values.get("DELETION_COMPLETE_DAYS", "").isdigit() and int(values["DELETION_COMPLETE_DAYS"]) > 30:
        errors.append("DELETION_COMPLETE_DAYS supera el maximo decidido (30)")
    psu = opt.get("PLAY_STORE_URL", "").strip()
    if psu and not psu.startswith("https://play.google.com/store/apps/details?id=com.pawgames.catbubbleisland"):
        errors.append("optional.PLAY_STORE_URL debe ser la ficha publica de com.pawgames.catbubbleisland")
    if errors and not preview:
        fail(errors)
    for e in errors:
        print("AVISO (preview): " + e)
    if d:
        values["EFFECTIVE_DATE_EN"] = d.strftime("%B ") + str(d.day) + d.strftime(", %Y")
        values["EFFECTIVE_DATE_ES"] = f"{d.day} de {MONTHS_ES[d.month - 1]} de {d.year}"
    today = datetime.date.today()
    values["BUILD_DATE"] = today.isoformat()
    values["YEAR"] = str(today.year)
    values["PLAY_STORE_URL"] = psu
    return cfg, values, opt

def apply_conditionals(text, flags):
    def repl(m):
        name, yes, no = m.group(1), m.group(2), m.group(4) or ""
        return yes if flags.get(name) else no
    pat = re.compile(r"<!--IF ([A-Z_]+)-->(.*?)(<!--ELSE \1-->(.*?))?<!--ENDIF \1-->", re.S)
    return pat.sub(repl, text)

def render(text, values, preview, rel):
    text = apply_conditionals(text, {"PLAY_STORE_URL": bool(values.get("PLAY_STORE_URL"))})
    def sub(m):
        k = m.group(1)
        v = values.get(k, "")
        if v:
            return v
        return f"[PENDIENTE: {k}]" if preview else m.group(0)
    text = MARKER.sub(sub, text)
    if rel.endswith(".html"):
        robots = '<meta name="robots" content="noindex, nofollow">' if preview else ""
        text = text.replace("<!--ROBOTS-->", robots)
        banner = ('<div class="preview-banner" role="note">VISTA PREVIA — datos sin verificar, no publicar · '
                  'PREVIEW — unverified data, do not publish</div>') if preview else ""
        text = text.replace("<!--PREVIEW_BANNER-->", banner)
    return text

# ---------- comprobaciones ----------
def img_size(path):
    with open(path, "rb") as f:
        head = f.read(32)
        if head[:8] == b"\x89PNG\r\n\x1a\n":
            return struct.unpack(">II", head[16:24])
        if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
            f.seek(12); data = f.read(30); chunk = data[:4]
            if chunk == b"VP8X":
                w = 1 + int.from_bytes(data[12:15], "little"); h = 1 + int.from_bytes(data[15:18], "little"); return w, h
            if chunk == b"VP8L":
                b = data[9:13]; w = 1 + (((b[1] & 0x3F) << 8) | b[0]); h = 1 + (((b[3] & 0xF) << 10) | (b[2] << 2) | ((b[1] & 0xC0) >> 6)); return w, h
            if chunk == b"VP8 ":
                w, h = struct.unpack("<HH", data[14:18]); return w & 0x3FFF, h & 0x3FFF
        if head[:2] == b"\xff\xd8":
            f.seek(2)
            while True:
                b = f.read(1)
                while b and b != b"\xff":
                    b = f.read(1)
                while b == b"\xff":
                    b = f.read(1)
                if not b:
                    return None
                m = b[0]
                if m in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                    f.read(3); h, w = struct.unpack(">HH", f.read(4)); return w, h
                ln = struct.unpack(">H", f.read(2))[0]; f.seek(ln - 2, 1)
    return None

class Scan(html.parser.HTMLParser):
    def __init__(self):
        super().__init__(); self.links = []; self.imgs = []; self.lang = None; self.title = False
        self.canonical = None; self.ids = set(); self.refresh = None
    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if "id" in a: self.ids.add(a["id"])
        if tag == "html": self.lang = a.get("lang")
        if tag == "title": self.title = True
        if tag == "link" and a.get("rel") == "canonical": self.canonical = a.get("href")
        if tag == "meta" and a.get("http-equiv") == "refresh": self.refresh = a.get("content", "").split("url=")[-1]
        for attr in ("href", "src"):
            if attr in a and tag != "link" or (tag == "link" and attr == "href" and a.get("rel") in ("stylesheet", "icon", "apple-touch-icon")):
                if attr in a: self.links.append(a[attr])
        if tag == "source" and "srcset" in a: self.links.append(a["srcset"])
        if tag == "img": self.imgs.append(a)

def check(out, site_url):
    errors = []
    base = site_url.rstrip("/")
    for dp, _, fs in os.walk(out):
        for fn in fs:
            p = os.path.join(dp, fn); rel = os.path.relpath(p, out).replace(os.sep, "/")
            if fn.endswith(TEXT_EXT) or fn in ("CNAME",):
                t = open(p, encoding="utf-8").read()
                for m in MARKER.finditer(t): errors.append(f"{rel}: marcador sin completar {m.group(0)}")
                if "[PENDIENTE:" in t: errors.append(f"{rel}: contiene [PENDIENTE:]")
                if re.search(r"<!--(IF|ELSE|ENDIF) ", t): errors.append(f"{rel}: bloque condicional sin resolver")
            if not fn.endswith(".html"):
                continue
            s = Scan(); s.feed(open(p, encoding="utf-8").read())
            if not s.lang: errors.append(f"{rel}: falta <html lang>")
            if not s.title: errors.append(f"{rel}: falta <title>")
            if fn != "404.html" and not s.canonical: errors.append(f"{rel}: falta canonical")
            for img in s.imgs:
                if "alt" not in img: errors.append(f"{rel}: <img> sin alt ({img.get('src')})")
            targets = list(s.links) + ([s.refresh] if s.refresh else [])
            for ln in targets:
                if not ln or ln.startswith(("mailto:", "tel:", "#")) or "[PENDIENTE:" in ln: continue
                if ln.startswith("http"):
                    if not (base and ln.startswith(base)): continue
                    path = os.path.join(out, ln[len(base):].lstrip("/"))
                else:
                    path = os.path.normpath(os.path.join(dp, ln))
                path, _, frag = path.partition("#")
                path = path.split("?")[0]
                if path.endswith("/") or os.path.isdir(path): path = os.path.join(path, "index.html")
                if not os.path.isfile(path):
                    errors.append(f"{rel}: enlace roto -> {ln}"); continue
                if frag and path.endswith(".html"):
                    s2 = Scan(); s2.feed(open(path, encoding="utf-8").read())
                    if frag not in s2.ids: errors.append(f"{rel}: ancla inexistente -> {ln}")
            for img in s.imgs:
                src = img.get("src", "")
                ip = os.path.join(out, src[len(base):].lstrip("/")) if src.startswith("http") else os.path.normpath(os.path.join(dp, src))
                if os.path.isfile(ip) and img.get("width") and img.get("height"):
                    sz = img_size(ip)
                    if sz and (str(sz[0]), str(sz[1])) != (img["width"], img["height"]):
                        errors.append(f"{rel}: {src} mide {sz[0]}x{sz[1]} pero declara {img['width']}x{img['height']}")
    return errors

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=os.path.join(HERE, "site-config.json"))
    ap.add_argument("--out", required=True)
    ap.add_argument("--preview", action="store_true")
    a = ap.parse_args()
    out = os.path.abspath(a.out)
    if os.path.abspath(SRC) == out or out.startswith(os.path.abspath(SRC) + os.sep):
        fail(["--out no puede estar dentro de Web/site"])
    cfg, values, opt = load_config(a.config, a.preview)
    if os.path.exists(out):
        shutil.rmtree(out)
    shutil.copytree(SRC, out)
    for dp, _, fs in os.walk(out):
        for fn in fs:
            if fn.endswith(TEXT_EXT):
                p = os.path.join(dp, fn); rel = os.path.relpath(p, out)
                t = open(p, encoding="utf-8").read()
                open(p, "w", encoding="utf-8", newline="\n").write(render(t, values, a.preview, rel))
    dom = opt.get("CUSTOM_DOMAIN", "").strip()
    if dom:
        if not values.get("SITE_URL", "").startswith("https://" + dom):
            fail([f"CUSTOM_DOMAIN={dom} no coincide con SITE_URL={values.get('SITE_URL')}"])
        open(os.path.join(out, "CNAME"), "w").write(dom + "\n")
    lines = [l.strip() for l in opt.get("APP_ADS_TXT_LINES", []) if l.strip()]
    if lines:
        open(os.path.join(out, "app-ads.txt"), "w", newline="\n").write("\n".join(lines) + "\n")
    errors = check(out, values.get("SITE_URL", ""))
    if a.preview:
        errors = [e for e in errors if "marcador" not in e and "PENDIENTE" not in e]
    if errors:
        fail(errors)
    n = sum(len(fs) for _, _, fs in os.walk(out))
    print(f"OK: {n} archivos en {out} ({'PREVIEW' if a.preview else 'ESTRICTO'})")

if __name__ == "__main__":
    main()
