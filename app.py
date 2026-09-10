from flask import Flask, Response
import requests
import re
import base64
import urllib3
from urllib.parse import urljoin, urlparse, parse_qs

# Silenciar warnings de SSL
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ============================================================
#  CONFIGURACIÓN
# ============================================================
BASE_URL = "https://futbollibrevip.pe"
AGENDA_URL = BASE_URL + "/agenda-data.php"
IMG_BASE = "https://img.futbollibrehd.com.pe"
IMAGEN_PREDETERMINADA = IMG_BASE + "/uploads/sin_imagen_d36205f0e8.png"
MAX_DEPTH = 6
USER_AGENT = "Mozilla/5.0 (Linux; Android 10; SM-G975F) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.120 Mobile Safari/537.36"

# ============================================================
#  FUNCIONES DE SCRAPING (Tu lógica original)
# ============================================================
def limpiar_texto(texto):
    if not texto:
        return ""
    return re.sub(r'\s+', ' ', str(texto)).strip()

def adaptar_url(url, base=BASE_URL):
    if not url:
        return None
    url = url.strip()
    if url.startswith("//"):
        return "https:" + url
    if url.startswith("http"):
        return url
    return urljoin(base, url)

def decodificar_param_r(url):
    try:
        parsed = urlparse(url)
        qs = parse_qs(parsed.query)
        if "r" in qs:
            b64 = qs["r"][0]
            padding = "=" * (-len(b64) % 4)
            decoded = base64.b64decode(b64 + padding).decode("utf-8", errors="ignore")
            if decoded.startswith("http"):
                return decoded
    except Exception:
        pass
    return None

def buscar_m3u8(texto, base_url):
    encontrados = []
    patrones = [
        r'["\']((?:https?:)?//[^"\'\s<>]+\.m3u8[^"\'\s<>]*)["\']',
        r'["\'](/[^"\'\s<>]+\.m3u8[^"\'\s<>]*)["\']',
        r'["\']([^"\'\s<>]+\.m3u8[^"\'\s<>]*)["\']',
        r'file\s*[:=]\s*["\']([^"\']+\.m3u8[^"\']*)["\']',
        r'source\s*[:=]\s*["\']([^"\']+\.m3u8[^"\']*)["\']',
        r'loadSource\s*\(\s*["\']([^"\']+\.m3u8[^"\']*)["\']',
        r'src\s*[:=]\s*["\']([^"\']+\.m3u8[^"\']*)["\']',
        r'url\s*[:=]\s*["\']([^"\']+\.m3u8[^"\']*)["\']',
    ]
    for patron in patrones:
        for match in re.findall(patron, texto, re.IGNORECASE):
            match = match.strip()
            if not match.startswith(("http://", "https://", "//", "/")): continue
            if any(c in match for c in [" ", "\n", "\t", "<", ">", "\\"]): continue
            url = adaptar_url(match, base_url)
            if not url or not url.startswith(("http://", "https://")) or ".m3u8" not in url: continue
            if any(x in url.lower() for x in ["ejemplo", "example", "test", "dummy", "sample", ".js", ".css", ".png", ".jpg", ".jpeg", ".gif", ".svg"]): continue
            try:
                p = urlparse(url)
                if "." not in p.netloc: continue
            except Exception: continue
            if url not in encontrados:
                encontrados.append(url)
    return encontrados

def buscar_iframes(texto, base_url):
    urls = []
    patrones = [
        r'<iframe[^>]+?src\s*=\s*["\']([^"\']+)["\']',
        r'iframe\.src\s*=\s*["\']([^"\']+)["\']',
        r'(?:player|embed|frame|video)\w*\.src\s*=\s*["\']([^"\']+)["\']',
        r'<iframe[^>]+?data-src\s*=\s*["\']([^"\']+)["\']',
    ]
    for patron in patrones:
        for match in re.findall(patron, texto, re.IGNORECASE):
            u = adaptar_url(match, base_url)
            if u: urls.append(u)
    return list(dict.fromkeys(urls))

def obtener_variante_maxima(url_m3u8, session, referer=None):
    try:
        headers = {"Referer": referer} if referer else {}
        r = session.get(url_m3u8, timeout=15, headers=headers)
        if r.status_code != 200: return url_m3u8
        contenido = r.text
        if "#EXT-X-STREAM-INF" not in contenido: return url_m3u8
        
        lineas = contenido.splitlines()
        variantes = []
        for i, linea in enumerate(lineas):
            if linea.startswith("#EXT-X-STREAM-INF"):
                bw_match = re.search(r'BANDWIDTH=(\d+)', linea)
                res_match = re.search(r'RESOLUTION=(\d+)x(\d+)', linea)
                bw = int(bw_match.group(1)) if bw_match else 0
                altura = int(res_match.group(2)) if res_match else 0
                for j in range(i + 1, len(lineas)):
                    l = lineas[j].strip()
                    if l and not l.startswith("#"):
                        variantes.append((bw, altura, adaptar_url(l, url_m3u8)))
                        break
        if not variantes: return url_m3u8
        variantes.sort(key=lambda x: (x[0], x[1]), reverse=True)
        return variantes[0][2]
    except Exception:
        return url_m3u8

def extraer_m3u8(url_pagina, session, profundidad=0, visitadas=None, referer=None):
    if visitadas is None: visitadas = set()
    if profundidad > MAX_DEPTH or url_pagina in visitadas: return []
    visitadas.add(url_pagina)
    try:
        headers = {"Referer": referer} if referer else {}
        r = session.get(url_pagina, timeout=15, allow_redirects=True, headers=headers)
        if r.status_code != 200: return []
        html = r.text
    except Exception: return []

    encontrados = buscar_m3u8(html, url_pagina)
    if encontrados: return encontrados

    if profundidad < MAX_DEPTH:
        iframes = buscar_iframes(html, url_pagina)
        iframes = [u for u in iframes if not any(x in u.lower() for x in ["doubleclick", "googleads", "googlesyndication", "popads", "acscdn", "trkr.ppof", "workers.dev/?", "facebook", "twitter"])]
        for ifr in iframes:
            if ifr in visitadas: continue
            sub = extraer_m3u8(ifr, session, profundidad + 1, visitadas, referer=url_pagina)
            if sub: return sub
    return []

def obtener_agenda(session):
    try:
        r = session.get(AGENDA_URL, timeout=15)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"Error agenda: {e}")
        return None

# ============================================================
#  SERVIDOR FLASK
# ============================================================
app = Flask(__name__)

@app.route('/')
def index():
    return "Servidor activo. Ve a /lista.m3u"

@app.route('/lista.m3u')
def servir_lista():
    print("Generando lista M3U...")
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    session.verify = False

    datos = obtener_agenda(session)
    if not datos or "data" not in datos:
        return Response("#EXTM3U\n# Error al obtener agenda.", mimetype='audio/x-mpegurl')

    lineas = ["#EXTM3U"]
    for evento in datos["data"]:
        attrs = evento.get("attributes", {})
        descripcion = limpiar_texto(attrs.get("diary_description", "Evento sin título"))
        
        img_url = IMAGEN_PREDETERMINADA
        try:
            ruta_img = attrs["country"]["data"]["attributes"]["image"]["data"]["attributes"]["url"]
            img_url = ruta_img if ruta_img.startswith("http") else IMG_BASE + ruta_img
        except (KeyError, TypeError): pass

        for embed in attrs.get("embeds", {}).get("data", []):
            embed_attrs = embed.get("attributes", {})
            nombre_canal = limpiar_texto(embed_attrs.get("embed_name", "Canal"))
            url_embed = adaptar_url(embed_attrs.get("embed_iframe", ""))
            if not url_embed: continue

            url_real = decodificar_param_r(url_embed) or url_embed
            m3u8_list = extraer_m3u8(url_real, session, referer=url_embed)

            if m3u8_list:
                m3u8_final = obtener_variante_maxima(m3u8_list[0], session, referer=url_real)
                titulo = limpiar_texto(f"{descripcion} - {nombre_canal}").replace('"', "'").replace(",", "·")
                lineas.append(f'#EXTINF:-1 tvg-logo="{img_url}", {titulo}')
                lineas.append(m3u8_final)
    
    return Response("\n".join(lineas) + "\n", mimetype='audio/x-mpegurl')

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080)
