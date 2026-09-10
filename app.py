from flask import Flask, Response, request, stream_with_context
import requests
import re
import base64
import urllib3
from urllib.parse import urljoin, urlparse, parse_qs
from concurrent.futures import ThreadPoolExecutor, as_completed
import time
import os
import threading

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ============================================================
#  CONFIG
# ============================================================
BASE_URL = "https://futbollibrevip.pe"
AGENDA_URL = BASE_URL + "/agenda-data.php"
IMG_BASE = "https://img.futbollibrehd.com.pe"
IMAGEN_PREDETERMINADA = IMG_BASE + "/uploads/sin_imagen_d36205f0e8.png"
MAX_DEPTH = 6
REFERER_DEFECTO = BASE_URL + "/"

# Calidad preferida: 1080, 720, 480 o None (máxima)
CALIDAD_PREFERIDA = 720

# Verificación de canales
VERIFICAR_CANALES = True
HILOS_VERIFICACION = 5
TIMEOUT_VERIFICACION = 12

# Refresco en background
REFRESCO_SEGUNDOS = 300   # regenerar cada 5 min
REINTENTO_TRAS_ERROR = 30 # si falla, reintentar en 30s

USER_AGENT = ("Mozilla/5.0 (Linux; Android 10; SM-G975F) "
              "AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/91.0.4472.120 Mobile Safari/537.36")

# ============================================================
#  CACHÉ GLOBAL + LOCK
# ============================================================
_cache = {
    "data": "#EXTM3U\n# Generando lista, espera unos segundos y recarga...\n",
    "timestamp": 0,
    "generando": False,
    "listo_alguna_vez": False,
}
_cache_lock = threading.Lock()

# ============================================================
#  SESIONES CON POOLING
# ============================================================
_session_global = requests.Session()
_session_global.headers.update({"User-Agent": USER_AGENT})
_session_global.verify = False
_session_global.mount("https://", requests.adapters.HTTPAdapter(
    pool_connections=20, pool_maxsize=20, max_retries=2
))
_session_global.mount("http://", requests.adapters.HTTPAdapter(
    pool_connections=20, pool_maxsize=20, max_retries=2
))

# ============================================================
#  HELPERS BASE64
# ============================================================
def b64_encode(s):
    if not s: return ""
    return base64.urlsafe_b64encode(s.encode()).decode().rstrip("=")

def b64_decode(s):
    if not s: return ""
    padding = "=" * (-len(s) % 4)
    try:
        return base64.urlsafe_b64decode(s + padding).decode()
    except Exception:
        return ""

def adaptar_url(url, base=BASE_URL):
    if not url: return None
    url = url.strip()
    if url.startswith("//"): return "https:" + url
    if url.startswith("http"): return url
    return urljoin(base, url)

def limpiar_texto(texto):
    if not texto: return ""
    return re.sub(r'\s+', ' ', str(texto)).strip()

def decodificar_param_r(url):
    try:
        parsed = urlparse(url)
        qs = parse_qs(parsed.query)
        if "r" in qs:
            b64 = qs["r"][0]
            padding = "=" * (-len(b64) % 4)
            decoded = base64.b64decode(b64 + padding).decode("utf-8", errors="ignore")
            if decoded.startswith("http"): return decoded
    except Exception:
        pass
    return None

# ============================================================
#  SCRAPING
# ============================================================
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
            if any(x in url.lower() for x in [
                "ejemplo", "example", "test", "dummy", "sample",
                ".js", ".css", ".png", ".jpg", ".jpeg", ".gif", ".svg"
            ]): continue
            try:
                p = urlparse(url)
                if "." not in p.netloc: continue
            except Exception:
                continue
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
        headers = {"Referer": referer, "User-Agent": USER_AGENT} if referer else {"User-Agent": USER_AGENT}
        r = session.get(url_m3u8, timeout=15, headers=headers, verify=False)
        if r.status_code != 200:
            return url_m3u8, ""
        contenido = r.text
        if "#EXT-X-STREAM-INF" not in contenido:
            return url_m3u8, ""

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

        if not variantes:
            return url_m3u8, ""

        if CALIDAD_PREFERIDA:
            exactas = [v for v in variantes if v[1] == CALIDAD_PREFERIDA]
            if exactas:
                variantes = exactas
            else:
                menores = [v for v in variantes if v[1] <= CALIDAD_PREFERIDA and v[1] > 0]
                if menores:
                    variantes = menores
                else:
                    variantes.sort(key=lambda x: (x[0], x[1]))
                    variantes = [variantes[0]]

        variantes.sort(key=lambda x: (x[0], x[1]), reverse=True)
        bw, h, url = variantes[0]
        calidad = f"{h}p" if h else f"{bw // 1000}kbps"
        return url, calidad

    except Exception:
        return url_m3u8, ""

def extraer_m3u8(url_pagina, session, profundidad=0, visitadas=None, referer=None):
    if visitadas is None: visitadas = set()
    if profundidad > MAX_DEPTH or url_pagina in visitadas: return []
    visitadas.add(url_pagina)
    try:
        headers = {"Referer": referer, "User-Agent": USER_AGENT} if referer else {"User-Agent": USER_AGENT}
        r = session.get(url_pagina, timeout=15, allow_redirects=True, headers=headers, verify=False)
        if r.status_code != 200: return []
        html = r.text
    except Exception:
        return []
    encontrados = buscar_m3u8(html, url_pagina)
    if encontrados: return encontrados
    if profundidad < MAX_DEPTH:
        iframes = buscar_iframes(html, url_pagina)
        iframes = [u for u in iframes if not any(x in u.lower() for x in [
            "doubleclick", "googleads", "googlesyndication", "popads",
            "acscdn", "trkr.ppof", "workers.dev/?", "facebook", "twitter"
        ])]
        for ifr in iframes:
            if ifr in visitadas: continue
            sub = extraer_m3u8(ifr, session, profundidad + 1, visitadas, referer=url_pagina)
            if sub: return sub
    return []

# ============================================================
#  VERIFICACIÓN DE CANAL
# ============================================================
def verificar_canal(url_m3u8, referer):
    try:
        headers = {
            "User-Agent": USER_AGENT,
            "Referer": referer or REFERER_DEFECTO,
            "Origin": (referer or REFERER_DEFECTO).rsplit("/", 1)[0],
            "Accept": "*/*",
        }

        r = requests.get(url_m3u8, headers=headers, timeout=TIMEOUT_VERIFICACION,
                         verify=False, allow_redirects=True)
        if r.status_code != 200:
            return False, f"HTTP {r.status_code}"
        texto = r.text
        if "#EXTM3U" not in texto:
            return False, "No es m3u8"

        primer_segmento = None
        for linea in texto.splitlines():
            l = linea.strip()
            if l and not l.startswith("#"):
                primer_segmento = adaptar_url(l, url_m3u8)
                break

        if not primer_segmento:
            return False, "Sin segmentos"

        h2 = dict(headers)
        h2["Range"] = "bytes=0-2048"
        r2 = requests.get(primer_segmento, headers=h2, timeout=TIMEOUT_VERIFICACION,
                          verify=False, stream=True, allow_redirects=True)

        if r2.status_code not in (200, 206):
            return False, f"Segmento HTTP {r2.status_code}"

        for chunk in r2.iter_content(chunk_size=1024):
            if chunk:
                r2.close()
                return True, "OK"
            break
        r2.close()
        return False, "Segmento vacío"

    except requests.exceptions.Timeout:
        return False, "Timeout"
    except Exception as e:
        return False, type(e).__name__

def probar_canal_completo(embed_info, session):
    nombre_canal = embed_info["nombre"]
    url_real = embed_info["url_real"]
    url_embed = embed_info["url_embed"]

    try:
        m3u8_list = extraer_m3u8(url_real, session, referer=url_embed)
        if not m3u8_list:
            return {**embed_info, "ok": False, "razon": "Sin m3u8"}

        m3u8_final, calidad = obtener_variante_maxima(m3u8_list[0], session, referer=url_real)

        if VERIFICAR_CANALES:
            ok, razon = verificar_canal(m3u8_final, referer=url_real)
            if not ok:
                return {**embed_info, "ok": False, "razon": razon}

        return {**embed_info, "ok": True, "m3u8": m3u8_final,
                "calidad": calidad, "referer": url_real}

    except Exception as e:
        return {**embed_info, "ok": False, "razon": type(e).__name__}

# ============================================================
#  FLASK
# ============================================================
app = Flask(__name__)

@app.route('/')
def index():
    with _cache_lock:
        listo = _cache["listo_alguna_vez"]
        generando = _cache["generando"]
        ts = _cache["timestamp"]
    return f"Servidor activo.<br>Listo: {listo}<br>Generando: {generando}<br>Última vez: {ts}"

def url_proxy(real_url, referer):
    u = b64_encode(real_url)
    r = b64_encode(referer or REFERER_DEFECTO)
    return f"/p?u={u}&r={r}"

@app.route('/p')
def proxy():
    u_b64 = request.args.get('u', '')
    r_b64 = request.args.get('r', '')
    real_url = b64_decode(u_b64)
    referer = b64_decode(r_b64) or REFERER_DEFECTO

    if not real_url:
        return "Falta url", 400

    headers = {
        "User-Agent": USER_AGENT,
        "Referer": referer,
        "Origin": referer.rsplit('/', 1)[0] if '/' in referer else referer,
        "Accept": "*/*",
        "Accept-Encoding": "identity",
    }

    if request.headers.get("Range"):
        headers["Range"] = request.headers["Range"]

    try:
        r = _session_global.get(
            real_url, headers=headers, timeout=30,
            verify=False, stream=True
        )
    except Exception as e:
        return f"Error: {e}", 502

    if r.status_code not in (200, 206):
        return f"Upstream {r.status_code}", r.status_code

    content_type = r.headers.get("Content-Type", "").lower()
    es_m3u8 = ("mpegurl" in content_type) or (".m3u8" in real_url.lower())

    if es_m3u8:
        texto = r.text
        nuevas_lineas = []
        for linea in texto.splitlines():
            l = linea.strip()
            if not l or l.startswith("#"):
                nuevas_lineas.append(linea)
                continue
            absoluta = urljoin(real_url, l)
            nuevas_lineas.append(url_proxy(absoluta, referer))
        return Response(
            "\n".join(nuevas_lineas),
            mimetype='application/vnd.apple.mpegurl',
            headers={"Access-Control-Allow-Origin": "*"}
        )
    else:
        def generar():
            try:
                for chunk in r.iter_content(chunk_size=256 * 1024):
                    if chunk:
                        yield chunk
            finally:
                r.close()

        resp_headers = {
            "Access-Control-Allow-Origin": "*",
            "Cache-Control": "no-cache",
        }
        for h in ["Content-Range", "Accept-Ranges", "Content-Length"]:
            if h in r.headers:
                resp_headers[h] = r.headers[h]

        return Response(
            stream_with_context(generar()),
            status=r.status_code,
            content_type=r.headers.get("Content-Type", "video/mp2t"),
            headers=resp_headers
        )

def generar_lista():
    print("=" * 60)
    print(f"🔄 Generando lista M3U (calidad: {CALIDAD_PREFERIDA or 'máxima'})")
    print("=" * 60)

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    session.verify = False
    session.mount("https://", requests.adapters.HTTPAdapter(
        pool_connections=10, pool_maxsize=10, max_retries=2
    ))

    try:
        r = session.get(AGENDA_URL, timeout=15)
        r.raise_for_status()
        datos = r.json()
    except Exception as e:
        print(f"❌ Error agenda: {e}")
        return None

    if not datos or "data" not in datos:
        return None

    embeds_a_probar = []
    for evento in datos["data"]:
        attrs = evento.get("attributes", {})
        descripcion = limpiar_texto(attrs.get("diary_description", "Evento sin título"))

        img_url = IMAGEN_PREDETERMINADA
        try:
            ruta_img = attrs["country"]["data"]["attributes"]["image"]["data"]["attributes"]["url"]
            img_url = ruta_img if ruta_img.startswith("http") else IMG_BASE + ruta_img
        except (KeyError, TypeError):
            pass

        for embed in attrs.get("embeds", {}).get("data", []):
            embed_attrs = embed.get("attributes", {})
            nombre_canal = limpiar_texto(embed_attrs.get("embed_name", "Canal"))
            url_embed = adaptar_url(embed_attrs.get("embed_iframe", ""))
            if not url_embed: continue
            url_real = decodificar_param_r(url_embed) or url_embed

            embeds_a_probar.append({
                "nombre": nombre_canal,
                "url_real": url_real,
                "url_embed": url_embed,
                "img": img_url,
                "descripcion": descripcion,
            })

    print(f"📋 Total canales a probar: {len(embeds_a_probar)}")

    resultados = []
    with ThreadPoolExecutor(max_workers=HILOS_VERIFICACION) as executor:
        futuros = [executor.submit(probar_canal_completo, e, session) for e in embeds_a_probar]
        for i, fut in enumerate(as_completed(futuros), 1):
            res = fut.result()
            resultados.append(res)
            if res["ok"]:
                print(f"   [{i}/{len(embeds_a_probar)}] ✅ {res['nombre']} ({res.get('calidad','')})")
            else:
                print(f"   [{i}/{len(embeds_a_probar)}] ❌ {res['nombre']} → {res.get('razon','?')}")

    lineas = ["#EXTM3U"]
    ok_count = 0
    for res in resultados:
        if not res["ok"]: continue
        titulo = limpiar_texto(f"{res['descripcion']} - {res['nombre']}").replace('"', "'").replace(",", "·")
        prox = url_proxy(res["m3u8"], res["referer"])
        lineas.append(f'#EXTINF:-1 tvg-logo="{res["img"]}", {titulo}')
        lineas.append(f"https://futbol-m3u.onrender.com{prox}")
        ok_count += 1

    print("=" * 60)
    print(f"✅ Canales funcionales: {ok_count} / {len(embeds_a_probar)}")
    print("=" * 60)

    return "\n".join(lineas) + "\n"

# ============================================================
#  BACKGROUND WORKER (genera la lista periódicamente)
# ============================================================
def worker_actualizacion():
    """Corre en segundo plano, regenerando la lista cada X segundos."""
    time.sleep(3)  # Esperar que Flask arranque

    while True:
        try:
            with _cache_lock:
                _cache["generando"] = True

            print("▶️  [WORKER] Iniciando generación en background...")
            inicio = time.time()
            contenido = generar_lista()
            duracion = time.time() - inicio

            if contenido and len(contenido) > 50:
                with _cache_lock:
                    _cache["data"] = contenido
                    _cache["timestamp"] = time.time()
                    _cache["listo_alguna_vez"] = True
                print(f"✅ [WORKER] Listo en {duracion:.1f}s. Próximo refresco en {REFRESCO_SEGUNDOS}s")
                espera = REFRESCO_SEGUNDOS
            else:
                print(f"⚠️  [WORKER] Lista vacía o error. Reintento en {REINTENTO_TRAS_ERROR}s")
                espera = REINTENTO_TRAS_ERROR

        except Exception as e:
            print(f"❌ [WORKER] Error: {e}")
            espera = REINTENTO_TRAS_ERROR
        finally:
            with _cache_lock:
                _cache["generando"] = False

        time.sleep(espera)

# ============================================================
#  RUTAS
# ============================================================
@app.route('/lista.m3u')
def servir_lista():
    """Devuelve SIEMPRE la caché al instante (nunca bloquea)."""
    with _cache_lock:
        contenido = _cache["data"]
        listo = _cache["listo_alguna_vez"]

    if not listo:
        print("⏳ Primera petición, aún generando...")

    # Headers que ayudan a los reproductores IPTV
    headers = {
        "Content-Type": "application/x-mpegurl",
        "Access-Control-Allow-Origin": "*",
        "Cache-Control": "no-cache, no-store, must-revalidate",
        "Pragma": "no-cache",
        "Expires": "0",
    }
    return Response(contenido, status=200, headers=headers)

@app.route('/refresh')
def refresh():
    """Fuerza un refresco inmediato en background."""
    with _cache_lock:
        ya_generando = _cache["generando"]

    if ya_generando:
        return "⏳ Ya se está generando, espera unos segundos."

    # Disparar generación en un hilo aparte
    def forzar():
        with _cache_lock:
            _cache["generando"] = True
        try:
            contenido = generar_lista()
            if contenido and len(contenido) > 50:
                with _cache_lock:
                    _cache["data"] = contenido
                    _cache["timestamp"] = time.time()
                    _cache["listo_alguna_vez"] = True
        finally:
            with _cache_lock:
                _cache["generando"] = False

    threading.Thread(target=forzar, daemon=True).start()
    return "🔄 Refresco lanzado. Espera 60s y recarga /lista.m3u"

# ============================================================
#  ARRANQUE
# ============================================================
def arrancar_worker():
    hilo = threading.Thread(target=worker_actualizacion, daemon=True)
    hilo.start()
    print("🚀 Worker de actualización iniciado")

# Arrancar el worker al importar el módulo (funciona con gunicorn)
arrancar_worker()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 5000)))
