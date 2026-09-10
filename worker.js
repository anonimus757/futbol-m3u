const REFERER = "https://futbollibrevip.pe/";
const USER_AGENT = "Mozilla/5.0 (Linux; Android 10; SM-G975F) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.120 Mobile Safari/537.36";

const cacheM3U8 = new Map();
const CACHE_TTL = 3 * 60 * 1000;

async function handlePlay(url) {
  const urlB64 = url.searchParams.get("url");
  if (!urlB64) {
    return new Response("Falta ?url=", { status: 400 });
  }

  let targetUrl;
  try {
    const b64 = urlB64.replace(/-/g, "+").replace(/_/g, "/");
    const padding = "=".repeat((4 - (b64.length % 4)) % 4);
    targetUrl = atob(b64 + padding);
  } catch (e) {
    return new Response("URL invalida", { status: 400 });
  }

  const cached = cacheM3U8.get(targetUrl);
  if (cached && Date.now() - cached.timestamp < CACHE_TTL) {
    return new Response(cached.content, {
      status: 200,
      headers: {
        "Content-Type": "application/vnd.apple.mpegurl",
        "Access-Control-Allow-Origin": "*",
        "Cache-Control": "no-cache"
      }
    });
  }

  const headers = {
    "User-Agent": USER_AGENT,
    "Referer": REFERER,
    "Origin": REFERER.replace(/\/$/, ""),
    "Accept": "*/*"
  };

  try {
    const res = await fetch(targetUrl, { headers: headers, redirect: "follow" });
    if (!res.ok) {
      return new Response("Origen devolvio " + res.status, { status: res.status });
    }
    const html = await res.text();

    let m3u8Url = null;
    const matches = html.match(/https?:\/\/[^"'\s<>\\]+\.m3u8[^"'\s<>\\]*/gi);
    if (matches && matches.length > 0) {
      m3u8Url = matches[0];
    }

    if (!m3u8Url) {
      const iframeMatch = html.match(/<iframe[^>]+src=["']([^"']+)["']/i);
      if (iframeMatch) {
        let iframeUrl = iframeMatch[1];
        if (iframeUrl.indexOf("//") === 0) {
          iframeUrl = "https:" + iframeUrl;
        } else if (iframeUrl.indexOf("/") === 0) {
          const u = new URL(targetUrl);
          iframeUrl = u.origin + iframeUrl;
        }
        const ires = await fetch(iframeUrl, { headers: headers, redirect: "follow" });
        const ihtml = await ires.text();
        const iMatches = ihtml.match(/https?:\/\/[^"'\s<>\\]+\.m3u8[^"'\s<>\\]*/gi);
        if (iMatches && iMatches.length > 0) {
          m3u8Url = iMatches[0];
        }
      }
    }

    if (!m3u8Url) {
      return new Response("No se encontro m3u8", { status: 404 });
    }

    const m3u8Res = await fetch(m3u8Url, { headers: headers, redirect: "follow" });
    if (!m3u8Res.ok) {
      return new Response("Error m3u8: " + m3u8Res.status, { status: m3u8Res.status });
    }
    const m3u8Text = await m3u8Res.text();

    const baseUrl = new URL(m3u8Url);
    const workerBase = url.origin;
    const lineas = m3u8Text.split("\n").map(function(line) {
      const l = line.trim();
      if (l && l.charAt(0) !== "#") {
        const abs = new URL(l, baseUrl).href;
        return workerBase + "/seg?url=" + encodeURIComponent(abs);
      }
      return line;
    });

    const content = lineas.join("\n");
    cacheM3U8.set(targetUrl, { content: content, timestamp: Date.now() });

    return new Response(content, {
      status: 200,
      headers: {
        "Content-Type": "application/vnd.apple.mpegurl",
        "Access-Control-Allow-Origin": "*",
        "Cache-Control": "no-cache"
      }
    });
  } catch (e) {
    return new Response("Error: " + e.message, { status: 502 });
  }
}

async function handleSeg(url, request) {
  const segUrl = url.searchParams.get("url");
  if (!segUrl) {
    return new Response("Falta ?url=", { status: 400 });
  }

  const decoded = decodeURIComponent(segUrl);
  const headers = {
    "User-Agent": USER_AGENT,
    "Referer": REFERER,
    "Origin": REFERER.replace(/\/$/, ""),
    "Accept": "*/*"
  };

  const range = request.headers.get("Range");
  if (range) {
    headers["Range"] = range;
  }

  try {
    const res = await fetch(decoded, { headers: headers, redirect: "follow" });
    if (!res.ok) {
      return new Response("Error seg: " + res.status, { status: res.status });
    }

    const respHeaders = {
      "Content-Type": res.headers.get("Content-Type") || "video/mp2t",
      "Access-Control-Allow-Origin": "*",
      "Cache-Control": "no-cache"
    };
    const cr = res.headers.get("Content-Range");
    if (cr) {
      respHeaders["Content-Range"] = cr;
    }
    const ar = res.headers.get("Accept-Ranges");
    if (ar) {
      respHeaders["Accept-Ranges"] = ar;
    }

    return new Response(res.body, { status: res.status, headers: respHeaders });
  } catch (e) {
    return new Response("Error: " + e.message, { status: 502 });
  }
}

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
