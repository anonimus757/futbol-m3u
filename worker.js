// ============================================================
//  WORKER PROXY PARA M3U8 Y SEGMENTOS .TS
// ============================================================

const REFERER_DEFECTO = "https://futbollibrevip.pe/";

export default {
  async fetch(request, env, ctx) {
    const urlObj = new URL(request.url);
    const targetUrl = urlObj.searchParams.get("url");

    if (!targetUrl) {
      return new Response("Falta ?url=", { status: 400 });
    }

    const decodedUrl = decodeURIComponent(targetUrl);
    const targetObj = new URL(decodedUrl);
    const hostname = targetObj.hostname;

    // 🔧 Headers que enviaremos al servidor real
    const headers = new Headers();
    headers.set("User-Agent", "Mozilla/5.0 (Linux; Android 10; SM-G975F) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.120 Mobile Safari/537.36");
    
    // 🔥 Referer y Origin según el dominio
    if (hostname.includes("ftlly.com")) {
      headers.set("Referer", REFERER_DEFECTO);
      headers.set("Origin", REFERER_DEFECTO.replace(/\/$/, ""));
    } else {
      headers.set("Referer", REFERER_DEFECTO);
      headers.set("Origin", REFERER_DEFECTO.replace(/\/$/, ""));
    }

    try {
      const response = await fetch(decodedUrl, {
        method: request.method,
        headers: headers,
        redirect: "follow",
      });

      const contentType = response.headers.get("Content-Type") || "";
      const esM3u8 = contentType.includes("mpegurl") || decodedUrl.includes(".m3u8");

      if (esM3u8) {
        const texto = await response.text();
        const baseUrl = new URL(decodedUrl);
        const workerBase = urlObj.origin;

        const lineasNuevas = texto.split("\n").map((linea) => {
          const l = linea.trim();
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

      return new Response(response.body, {
        status: response.status,
        headers: {
          "Content-Type": contentType || "video/mp2t",
          "Access-Control-Allow-Origin": "*",
          "Cache-Control": "no-cache",
          ...(response.headers.has("Content-Range") && {
            "Content-Range": response.headers.get("Content-Range"),
          }),
          ...(response.headers.has("Accept-Ranges") && {
            "Accept-Ranges": response.headers.get("Accept-Ranges"),
          }),
        },
      });
    } catch (e) {
      return new Response(`Error: ${e.message}`, { status: 502 });
    }
  },
};
