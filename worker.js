// ============================================================
//  WORKER PROXY PARA HLS (M3U8) CON SOPORTE DE CABECERAS
// ============================================================

// 🔧 CONFIGURACIÓN: Define las reglas para cada dominio de origen.
// Esto es clave para saltar el bloqueo por Referer/Origin.
const rules = {
    // Para los canales de ftlly.com
    "ftlly.com": {
        "Referer": "https://futbollibrevip.pe/",
        "Origin": "https://futbollibrevip.pe"
    },
    // Puedes añadir más dominios aquí si otros canales fallan.
};

export default {
    async fetch(request, env, ctx) {
        const urlObj = new URL(request.url);
        const targetUrl = urlObj.searchParams.get("url");

        if (!targetUrl) {
            return new Response("Falta el parámetro ?url=", { status: 400 });
        }

        const decodedUrl = decodeURIComponent(targetUrl);
        const targetObj = new URL(decodedUrl);
        const hostname = targetObj.hostname;

        // 🛠️ Construir los encabezados que se enviarán al servidor real
        const headers = new Headers();
        // Usar un User-Agent de navegador es crucial para no ser bloqueado
        headers.set("User-Agent", "Mozilla/5.0 (Linux; Android 10; SM-G975F) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.120 Mobile Safari/537.36");

        // Aplicar las reglas de encabezados según el dominio
        for (const [dominio, reglas] of Object.entries(rules)) {
            if (hostname.includes(dominio)) {
                for (const [clave, valor] of Object.entries(reglas)) {
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
