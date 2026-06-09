"""
OTC Financial Markets → Telegram Bot
=====================================
Scrapea la sección de noticias 24/7 de otcfinancialmarkets.com
y reenvía automáticamente las noticias nuevas a un canal/grupo de Telegram.

REQUISITOS:
    pip install playwright requests python-dotenv
    playwright install chromium

CONFIGURACIÓN:
    Edita las variables BOT_TOKEN y CHAT_ID abajo,
    o crea un archivo .env en la misma carpeta con:
        BOT_TOKEN=123456789:ABCdef...
        CHAT_ID=-100123456789
"""

import os
import json
import time
import hashlib
import logging
import requests
from datetime import datetime
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

# ─────────────────────────────────────────────
# CONFIGURACIÓN — Edita esto
# ─────────────────────────────────────────────
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "PON_AQUI_TU_BOT_TOKEN")
CHAT_ID   = os.getenv("CHAT_ID",   "PON_AQUI_TU_CHAT_ID")   # Ej: @AurumcommunityFX o -100123456789
TOPIC_ID  = os.getenv("TOPIC_ID",  "")                       # ID del tema (ej: 15). Vacío = chat general

URL_NOTICIAS    = "https://otcfinancialmarkets.com/noticias-24h"
INTERVALO_SEG   = 120          # Revisar cada 2 minutos (ajustable)
ARCHIVO_VISTOS  = "noticias_enviadas.json"

# ─────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
log = logging.getLogger(__name__)


# ── Persistencia de noticias ya enviadas ──────
def cargar_enviadas() -> set:
    if os.path.exists(ARCHIVO_VISTOS):
        with open(ARCHIVO_VISTOS, "r", encoding="utf-8") as f:
            return set(json.load(f))
    return set()

def guardar_enviadas(ids: set):
    # Guardar solo los últimos 500 para no crecer indefinidamente
    lista = list(ids)[-500:]
    with open(ARCHIVO_VISTOS, "w", encoding="utf-8") as f:
        json.dump(lista, f)


# ── Scraping con Playwright (maneja JS) ───────
def obtener_noticias() -> list[dict]:
    """
    Devuelve lista de dicts: [{"id": ..., "titulo": ..., "texto": ..., "url": ...}]
    Busca en la página principal Y dentro de iframes.
    Si no encuentra nada, guarda debug.html y debug.png para diagnóstico.
    """
    noticias = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
        )

        try:
            page.goto(URL_NOTICIAS, timeout=45000, wait_until="domcontentloaded")
            # Espera larga para que cargue todo el contenido dinámico
            page.wait_for_timeout(10000)

            # ── Selectores EXACTOS de otcfinancialmarkets.com ──
            # Cada noticia es una tarjeta: div.rounded-lg.border.shadow-sm.overflow-hidden
            #   - hora:  span.font-mono  (ej: "10/6, 00:59:24")
            #   - texto: p.break-words
            #   - tags:  div.bg-secondary (Market Regions, Energy...)
            page.wait_for_selector("p.break-words", timeout=20000)
            tarjetas = page.query_selector_all("div.rounded-lg.border.shadow-sm.overflow-hidden")
            log.info(f"Tarjetas de noticia detectadas: {len(tarjetas)}")

            for tarjeta in tarjetas[:30]:
                try:
                    texto_el = tarjeta.query_selector("p.break-words")
                    if not texto_el:
                        continue
                    texto = texto_el.inner_text().strip()
                    if len(texto) < 30:
                        continue

                    hora_el = tarjeta.query_selector("span.font-mono")
                    hora = hora_el.inner_text().strip() if hora_el else ""

                    tags_els = tarjeta.query_selector_all("div.bg-secondary")
                    tags = [t.inner_text().strip() for t in tags_els if t.inner_text().strip()]

                    # ID único: hash de hora + inicio del texto
                    uid = hashlib.md5((hora + texto[:150]).encode()).hexdigest()[:12]

                    noticias.append({
                        "id":     uid,
                        "titulo": texto[:90] + ("…" if len(texto) > 90 else ""),
                        "texto":  texto,
                        "hora":   hora,
                        "tags":   tags,
                        "url":    URL_NOTICIAS,
                    })
                except Exception as e:
                    log.debug(f"Error procesando tarjeta: {e}")

            if not noticias:
                # ── MODO DIAGNÓSTICO ──
                log.warning("⚠️  No se encontró contenido de noticias.")
                log.warning("    Guardando debug.html y debug.png para diagnóstico...")
                try:
                    with open("debug.html", "w", encoding="utf-8") as f:
                        f.write(page.content())
                    page.screenshot(path="debug.png", full_page=True)
                    log.warning("    Archivos guardados en la carpeta del bot.")
                except Exception as e:
                    log.error(f"    No se pudo guardar diagnóstico: {e}")

        except Exception as e:
            log.error(f"Error al cargar la página: {e}")
        finally:
            browser.close()

    return noticias


# ── Envío a Telegram ──────────────────────────
def enviar_telegram(noticia: dict) -> bool:
    """Envía una noticia al canal de Telegram."""
    titulo = noticia["titulo"]
    texto  = noticia["texto"]
    url    = noticia["url"]
    hora   = noticia.get("hora", "")
    tags   = noticia.get("tags", [])

    # Envía el flash completo. Solo se trunca si supera el límite
    # de Telegram (4096 caracteres por mensaje).
    cuerpo = texto if len(texto) <= 3500 else texto[:3500] + "…"

    linea_tags = " ".join(f"\\#{escapar_md(t).replace(' ', '')}" for t in tags[:4])

    mensaje = (
        f"📰 {escapar_md(cuerpo)}\n\n"
        + (f"🕐 {escapar_md(hora)}\n" if hora else "")
        + (f"{linea_tags}\n" if linea_tags else "")
        + f"🔗 [OTC Financial Markets]({url})"
    )

    endpoint = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload  = {
        "chat_id":    CHAT_ID,
        "text":       mensaje,
        "parse_mode": "MarkdownV2",
        "disable_web_page_preview": False,
    }

    # Si hay un tema (topic) configurado, envía al tema concreto
    if TOPIC_ID:
        payload["message_thread_id"] = int(TOPIC_ID)

    try:
        r = requests.post(endpoint, json=payload, timeout=10)
        if r.status_code == 200:
            log.info(f"✅ Enviado: {titulo[:60]}")
            return True
        elif r.status_code == 429:
            # Telegram pide esperar X segundos (límite anti-spam)
            try:
                espera = r.json()["parameters"]["retry_after"] + 1
            except Exception:
                espera = 20
            log.warning(f"⏸  Límite de Telegram alcanzado. Esperando {espera}s y reintentando...")
            time.sleep(espera)
            r2 = requests.post(endpoint, json=payload, timeout=10)
            if r2.status_code == 200:
                log.info(f"✅ Enviado (reintento): {titulo[:60]}")
                return True
            log.error(f"❌ Falló el reintento: {r2.text[:150]}")
            return False
        else:
            log.error(f"❌ Error Telegram {r.status_code}: {r.text[:200]}")
            return False
    except Exception as e:
        log.error(f"❌ Excepción al enviar: {e}")
        return False


def escapar_md(texto: str) -> str:
    """Escapa caracteres especiales para MarkdownV2 de Telegram."""
    especiales = r"\_*[]()~`>#+-=|{}.!"
    return "".join(f"\\{c}" if c in especiales else c for c in texto)


# ── Bucle principal ───────────────────────────
def main():
    log.info("=" * 50)
    log.info("OTC Financial Markets → Telegram Bot")
    log.info(f"Canal: {CHAT_ID}")
    log.info(f"Intervalo: {INTERVALO_SEG}s")
    log.info("=" * 50)

    if "PON_AQUI" in BOT_TOKEN or "PON_AQUI" in str(CHAT_ID):
        log.error("⚠️  Configura BOT_TOKEN y CHAT_ID antes de ejecutar.")
        log.error("   Edita el archivo .env o las variables al inicio del script.")
        return

    enviadas = cargar_enviadas()
    log.info(f"Noticias ya enviadas en historial: {len(enviadas)}")

    # RUN_ONCE=1 → revisa una vez y termina (modo GitHub Actions)
    run_once = os.getenv("RUN_ONCE", "0") == "1"

    while True:
        log.info(f"🔍 Revisando noticias... ({datetime.now().strftime('%H:%M:%S')})")
        try:
            noticias = obtener_noticias()
            log.info(f"   Encontradas: {len(noticias)}")

            nuevas = [n for n in noticias if n["id"] not in enviadas]
            log.info(f"   Nuevas: {len(nuevas)}")

            for noticia in reversed(nuevas):  # Envía en orden cronológico
                exito = enviar_telegram(noticia)
                if exito:
                    enviadas.add(noticia["id"])
                    time.sleep(4)  # Pausa entre mensajes (límite Telegram: ~20/min por grupo)

            guardar_enviadas(enviadas)

        except KeyboardInterrupt:
            log.info("Bot detenido por el usuario.")
            break
        except Exception as e:
            log.error(f"Error inesperado: {e}")

        if run_once:
            log.info("Modo ejecución única: terminado.")
            break

        log.info(f"⏳ Esperando {INTERVALO_SEG}s...")
        time.sleep(INTERVALO_SEG)


if __name__ == "__main__":
    main()
