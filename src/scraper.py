"""Extrae datos crudos de los portales de investinperu.pe usando Playwright."""

import asyncio
import json
import random
import time
from datetime import date
from pathlib import Path

import yaml
from playwright.async_api import async_playwright, Page, Route

RAW_BASE = Path("data/raw")
FILTERS_PATH = Path("config/filters.yaml")


def load_config() -> dict:
    with open(FILTERS_PATH) as f:
        return yaml.safe_load(f)


def raw_dir(run_date: date) -> Path:
    d = RAW_BASE / run_date.isoformat()
    d.mkdir(parents=True, exist_ok=True)
    return d


async def pause():
    await asyncio.sleep(random.uniform(2, 4))


class XHRCapture:
    """Captura respuestas JSON de la página para detectar APIs internas."""

    def __init__(self):
        self.responses: list[dict] = []

    async def attach(self, page: Page):
        async def on_response(response):
            ct = response.headers.get("content-type", "")
            if "json" in ct and response.status == 200:
                try:
                    body = await response.json()
                    self.responses.append({
                        "url": response.url,
                        "status": response.status,
                        "body": body,
                    })
                except Exception:
                    pass

        page.on("response", on_response)


async def scrape_portal(page: Page, portal_id: str, portal_cfg: dict, out_dir: Path) -> dict:
    """Intenta extraer datos de un portal. Devuelve el manifest de la corrida."""
    url = portal_cfg["url"]
    manifest = {
        "portal": portal_id,
        "url": url,
        "metodo": None,
        "total_ui": None,
        "total_extraido": 0,
        "filtros_aplicados": [],
        "errores": [],
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

    capture = XHRCapture()
    await capture.attach(page)

    print(f"[{portal_id}] Navegando a {url}")
    try:
        await page.goto(url, wait_until="networkidle", timeout=60000)
    except Exception as e:
        manifest["errores"].append(f"goto falló: {e}")
        return manifest

    await pause()

    # Intento 1: buscar endpoint JSON en el tráfico capturado
    api_data = _find_api_data(capture.responses)
    if api_data is not None:
        manifest["metodo"] = "api_interna"
        out_file = out_dir / f"{portal_id}.json"
        out_file.write_text(json.dumps(api_data, ensure_ascii=False, indent=2))
        manifest["total_extraido"] = _count_records(api_data)
        print(f"[{portal_id}] API interna: {manifest['total_extraido']} registros → {out_file}")
        return manifest

    # Intento 2: botón Exportar a Excel
    try:
        export_btn = page.locator("text=Exportar a Excel, text=Exportar Excel, text=Export").first
        if await export_btn.is_visible(timeout=3000):
            async with page.expect_download(timeout=30000) as dl_info:
                await export_btn.click()
                await pause()
            dl = await dl_info.value
            out_file = out_dir / f"{portal_id}.xlsx"
            await dl.save_as(str(out_file))
            manifest["metodo"] = "exportar_excel"
            manifest["total_extraido"] = -1  # se contará en normalización
            print(f"[{portal_id}] Excel descargado → {out_file}")
            return manifest
    except Exception as e:
        manifest["errores"].append(f"export button: {e}")

    # Intento 3: parseo DOM + paginación
    rows = await _scrape_dom(page, portal_id)
    if rows:
        manifest["metodo"] = "dom"
        out_file = out_dir / f"{portal_id}.json"
        out_file.write_text(json.dumps(rows, ensure_ascii=False, indent=2))
        manifest["total_extraido"] = len(rows)
        print(f"[{portal_id}] DOM: {len(rows)} filas → {out_file}")
    else:
        manifest["errores"].append("Sin datos: ningún método funcionó")
        print(f"[{portal_id}] ADVERTENCIA: no se obtuvieron datos")

    return manifest


def _find_api_data(responses: list[dict]):
    """Busca la respuesta JSON con más registros (probablemente la lista de proyectos)."""
    best = None
    best_count = 0
    for r in responses:
        body = r["body"]
        count = _count_records(body)
        if count > best_count:
            best = {"endpoint": r["url"], "data": body}
            best_count = count
    return best if best_count > 0 else None


def _count_records(data) -> int:
    if isinstance(data, list):
        return len(data)
    if isinstance(data, dict):
        for key in ("data", "items", "results", "proyectos", "registros"):
            if isinstance(data.get(key), list):
                return len(data[key])
        return sum(_count_records(v) for v in data.values() if isinstance(v, (list, dict)))
    return 0


async def _scrape_dom(page: Page, portal_id: str) -> list[dict]:
    """Extrae filas de tablas o tarjetas renderizadas y recorre toda la paginación."""
    rows = []
    page_num = 1
    while True:
        # Espera que la tabla esté presente
        try:
            await page.wait_for_selector("table tr, .card-proyecto, .proyecto-item", timeout=10000)
        except Exception:
            break

        new_rows = await page.evaluate("""() => {
            const rows = [];
            // Intentar tabla estándar
            document.querySelectorAll('table tbody tr').forEach(tr => {
                const cells = Array.from(tr.querySelectorAll('td')).map(td => td.innerText.trim());
                if (cells.length > 2) rows.push({_cells: cells});
            });
            // Intentar tarjetas
            if (rows.length === 0) {
                document.querySelectorAll('[class*="proyecto"], [class*="card"]').forEach(card => {
                    rows.push({_html: card.innerHTML, _text: card.innerText.trim()});
                });
            }
            return rows;
        }""")

        if not new_rows:
            break

        rows.extend(new_rows)
        print(f"  página {page_num}: +{len(new_rows)} registros")

        # Siguiente página
        next_btn = page.locator("a:has-text('Siguiente'), a:has-text('>'), [aria-label='Next']").first
        try:
            if await next_btn.is_visible(timeout=2000):
                await next_btn.click()
                await pause()
                page_num += 1
            else:
                break
        except Exception:
            break

    return rows


async def run_scraper(portales: list[str] | None, run_date: date, dry_run: bool = False) -> dict:
    """Punto de entrada. Si dry_run=True, solo procesa el primer portal."""
    config = load_config()
    portales_cfg = config["portales"]
    out_dir = raw_dir(run_date)
    manifests = {}

    targets = portales or list(portales_cfg.keys())
    if dry_run:
        targets = targets[:1]
        print(f"[DRY RUN] Solo procesando {targets[0]}")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx = await browser.new_context(
            user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = await ctx.new_page()

        for pid in targets:
            if pid not in portales_cfg:
                print(f"[WARN] Portal {pid} no definido en config/filters.yaml")
                continue
            manifest = await scrape_portal(page, pid, portales_cfg[pid], out_dir)
            manifests[pid] = manifest
            await pause()

        await browser.close()

    manifest_file = out_dir / "manifest.json"
    manifest_file.write_text(json.dumps(manifests, ensure_ascii=False, indent=2))
    print(f"\nManifest guardado: {manifest_file}")
    return manifests
