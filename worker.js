// ============================================================
//  WORKER TODO-EN-UNO: genera token + reproduce video
//  Misma IP para todo = sin bloqueo por hotlinking
// ============================================================

const REFERER = "https://futbollibrevip.pe/";
const USER_AGENT = "Mozilla/5.0 (Linux; Android 10; SM-G975F) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.120 Mobile Safari/537.36";

// Caché simple en memoria (vive mientras el Worker esté activo)
const cacheM3U8 = new Map();
const CACHE_TTL = 3 * 60 * 1000; // 3 minutos

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);

    if (url.pathname === "/play") {
      return handlePlay(url);
    }
    if (url.pathname === "/seg") {
      return handleSeg(url, request);
    }
    return new Response("Worker activo. Usa /play?url=... y /seg?url=...", { status: 200 });
  }
};

// ============================================================
//  /play?url=BASE64 → devuelve m3u8 con segmentos proxificados
// ============================================================
async function handlePlay(url) {
  const urlB64 = url.searchParams.get("url");
  if (!urlB64) return new Response("Falta ?url=", { status: 400 });

  let targetUrl;
  try {
    // Aceptar base64 url-safe y base64 normal
    const b64 = urlB64.replace(/-/g, "+").replace(/_/g, "/");
    const padding = "=".repeat((4 - (b64.length % 4)) % 4);
    targetUrl = atob(b64 + padding);
  } catch (e) {
    return new Response("URL inválida", { status: 400 });
  }

  // Verificar caché
  const cached = cacheM3U8.get(targetUrl);
  if (cached && Date.now() - cached.timestamp < CACHE_TTL) {
    return new Response(cached.content, {
      status: 200,
      headers: {
        "Content-Type": "application/vnd.apple.mpegurl",
        "Access-Control-Allow-Origin": "*",
        "Cache-Control": "no-cache",
      },
    });
  }

  // Headers para el origen
  const headers = {
    "User-Agent": USER_AGENT,
    "Referer": REFERER,
    "Origin": REFERER.replace(/\/$/, ""),
    "Accept": "*/*",
  };

  try {
    // 1. Fetch del embed/tvf90 para obtener el m3u8
    const res = await fetch(targetUrl, { headers, redirect: "follow" });
    if (!res.ok) {
      return new Response(`Origen devolvió ${res.status}`, { status: res.status });
    }
    const html = await res.text();

    // 2. Buscar el m3u8 en el HTML
    let m3u8Url = null;
    const m3u8Matches = html.match(/https?:\/\/[^"'\s<>\\]+\.m3u8[^"'\s<>\\]*/gi);
    if (m3u8Matches && m3u8Matches.length > 0) {
      m3u8Url = m3u8Matches[0];
    }

    // 3. Si no se encuentra, buscar en iframes anidados (1 nivel)
    if (!m3u8Url) {
      const iframeMatch = html.match(/<iframe[^>]+src=["']([^"']+)["']/i);
      if (iframeMatch) {
        let iframeUrl = iframeMatch[1];
        if (iframeUrl.startsWith("//")) iframeUrl = "https:" + iframeUrl;
        else if (iframeUrl.startsWith("/")) {
          const u = new URL(targetUrl);
          iframeUrl = u.origin + iframeUrl;
        }
        const ires = await fetch(iframeUrl, { headers, redirect: "follow" });
        const ihtml = await ires.text();
        const iMatches = ihtml.match(/https?:\/\/[^"'\s<>\\]+\.m3u8[^"'\s<>\\]*/gi);
        if (iMatches && iMatches.length > 0) m3u8Url = iMatches[0];
      }
    }

    if (!m3u8Url) {
      return new Response("No se encontró m3u8", { status: 404 });
    }

    // 4. Fetch del m3u8 con la MISMA IP de Cloudflare (crucial)
    const m3u8Res = await fetch(m3u8Url, { headers, redirect: "follow" });
    if (!m3u8Res.ok) {
      return new Response(`Error m3u8: ${m3u8Res.status}`, { status: m3u8Res.status });
    }
    const m3u8Text = await m3u8Res.text();

    // 5. Reescribir las URLs internas para que pasen por /seg
    const baseUrl = new URL(m3u8Url);
    const workerBase = url.origin;

    const lineas = m3u8Text.split("\n").map((line) => {
      const l = line.trim();
      if (l && !l.startsWith("#")) {
        const abs = new URL(l, baseUrl).href;
        return `${workerBase}/seg?url=${encodeURIComponent(abs)}`;
      }
      return line;
    });

    const content = lineas.join("\n");

    // Guardar en caché
    cacheM3U8.set(targetUrl, { content, timestamp: Date.now() });
    // Limpieza básica de caché
    if (cacheM3U8.size > 100) {
      const oldestKey = cacheM3U8.keys().next().value;
      cacheM3U8.delete(oldestKey);
    }

    return new Response(content, {
      status: 200,
      headers: {
        "Content-Type": "application/vnd.apple.mpegurl",
        "Access-Control-Allow-Origin": "*",
        "Cache-Control": "no-cache",
      },
    });

  } catch (e) {
    return new Response(`Error: ${e.message}`, { status: 502 });
  }
}

// ============================================================
//  /seg?url=... → retransmite segmentos .ts con la misma IP
// ============================================================
async function handleSeg(url, request) {
  const segUrl = url.searchParams.get("url");
  if (!segUrl) return new Response("Falta ?url=", { status: 400 });

  const decoded = decodeURIComponent(segUrl);

  const headers = {
    "User-Agent": USER_AGENT,
    "Referer": REFERER,
    "Origin": REFERER.replace(/\/$/, ""),
    "Accept": "*/*",
  };

  // Pasar Range si el cliente lo pide
  if (request.headers.get("Range")) {
    headers["Range"] = request.headers.get("Range");
  }

  try {
    const res = await fetch(decoded, { headers, redirect: "follow" });
    if (!res.ok) {
      return new Response(`Error seg: ${res.status}`, { status: res.status });
    }

    const respHeaders = {
      "Content-Type": res.headers.get("Content-Type") || "video/mp2t",
      "Access-Control-Allow-Origin": "*",
      "Cache-Control": "no-cache",
    };
    if (res.headers.get("Content-Range")) respHeaders["Content-Range"] = res.headers.get("Content-Range");
    if (res.headers.get("Accept-Ranges")) respHeaders["Accept-Ranges"] = res.headers.get("Accept-Ranges");

    return new Response(res.body, { status: res.status, headers: respHeaders });
  } catch (e) {
    return new Response(`Error: ${e.message}`, { status: 502 });
  }
}                for (const [clave, valor] of Object.entries(reglas)) {
                    headers.set(clave, valor);
                }
                break;
            }
        }

        try {
            const response = await fetch(decodedUrl, {
                method: request.method,
                headers: headers,
                redirect: "follow",
            });

            // Si el servidor de origen devuelve un error, lo mostramos para depurar
            if (!response.ok) {
                return new Response(`Error del servidor de origen: ${response.status} ${response.statusText}`, { 
                    status: response.status,
                    headers: { "Content-Type": "text/plain" }
                });
            }

            const contentType = response.headers.get("Content-Type") || "";
            const esM3u8 = contentType.includes("mpegurl") || decodedUrl.includes(".m3u8");

            // 🔄 Si es un archivo .m3u8, reescribir las URLs internas
            if (esM3u8) {
                const texto = await response.text();
                const baseUrl = new URL(decodedUrl);
                const workerBase = urlObj.origin;

                const lineasNuevas = texto.split("\n").map((linea) => {
                    const l = linea.trim();
                    // Si no es un comentario (#EXT...) y no está vacía, la proxificamos
                    if (l && !l.startsWith("#")) {
                        const urlAbsoluta = new URL(l, baseUrl).href;
                        return `${workerBase}/?url=${encodeURIComponent(urlAbsoluta)}`;
                    }
                    return linea;
                });

                return new Response(lineasNuevas.join("\n"), {
                    status: 200,
                    headers: {
                        "Content-Type": "application/vnd.apple.mpegurl",
                        "Access-Control-Allow-Origin": "*",
                        "Cache-Control": "no-cache",
                    },
                });
            }

            // 📦 Si es un segmento de video (.ts, .m4s, etc.), lo pasamos directamente
            return new Response(response.body, {
                status: response.status,
                headers: {
                    "Content-Type": contentType || "video/mp2t",
                    "Access-Control-Allow-Origin": "*",
                    "Cache-Control": "no-cache",
                    // Propagar encabezados de rango para permitir el "seek"
                    ...(response.headers.has("Content-Range") && {
                        "Content-Range": response.headers.get("Content-Range"),
                    }),
                    ...(response.headers.has("Accept-Ranges") && {
                        "Accept-Ranges": response.headers.get("Accept-Ranges"),
                    }),
                },
            });
        } catch (e) {
            // Capturamos cualquier error de red o de fetch
            return new Response(`Error en el proxy: ${e.message}`, { status: 502 });
        }
    },
};
