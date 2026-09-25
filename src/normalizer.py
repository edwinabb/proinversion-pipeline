"""Convierte snapshots crudos al esquema normalizado y deduplica."""

import json
import re
import unicodedata
from datetime import date
from pathlib import Path

import pandas as pd
import yaml

SCHEMA_COLS = [
    "id_interno", "portal", "modalidad", "nombre", "sector_funcion", "entidad",
    "nivel_gobierno", "region", "provincia", "distrito", "cui", "monto_soles",
    "monto_usd", "fase_estado", "fecha_adjudicacion_estimada", "tipo_iniciativa",
    "fuente_url", "fecha_extraccion", "campos_faltantes", "fit_nivel", "fit_razon",
    "timing", "prioridad", "notas",
]

PROCESSED_DIR = Path("data/processed")
DOCS_DIR = Path("docs")
TIPO_CAMBIO = 3.40


def normalize_text(s: str) -> str:
    if not isinstance(s, str):
        return ""
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return s.lower().strip()


def parse_monto(raw) -> float | None:
    if pd.isna(raw) or raw == "" or raw is None:
        return None
    s = str(raw).strip()
    # elimina prefijos de moneda
    s = re.sub(r"(?i)s/\.?\s*|us\$\s*|\$\s*", "", s)
    # "millones"
    millones = "millon" in s.lower() or "million" in s.lower()
    s = re.sub(r"(?i)millones?", "", s).strip()
    # normaliza separadores: "1.234,56" → "1234.56" y "1,234.56" → "1234.56"
    if "," in s and "." in s:
        if s.index(".") < s.index(","):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        # Si hay más de una coma o la parte tras la última coma tiene ≠ 2 dígitos → separador de miles
        parts = s.split(",")
        if len(parts) > 2 or (len(parts) == 2 and len(parts[-1]) == 3):
            s = s.replace(",", "")
        else:
            s = s.replace(",", ".")
    s = re.sub(r"[^\d.]", "", s)
    try:
        val = float(s)
        return val * 1_000_000 if millones else val
    except ValueError:
        return None


def load_raw(portal_id: str, run_date: date) -> pd.DataFrame | None:
    raw_dir = Path("data/raw") / run_date.isoformat()
    for ext in ("json", "xlsx", "html"):
        f = raw_dir / f"{portal_id}.{ext}"
        if not f.exists():
            continue
        if ext == "json":
            data = json.loads(f.read_text())
            if isinstance(data, list):
                return pd.DataFrame(data)
            for key in ("data", "items", "results", "proyectos", "registros"):
                if isinstance(data.get("data", {}).get(key) if isinstance(data.get("data"), dict) else data.get(key), list):
                    records = data.get("data", {}).get(key) or data.get(key)
                    return pd.DataFrame(records)
            # API interna wrapper
            if "data" in data:
                inner = data["data"]
                if isinstance(inner, list):
                    return pd.DataFrame(inner)
                for key in ("data", "items", "results", "proyectos"):
                    if isinstance(inner.get(key), list):
                        return pd.DataFrame(inner[key])
            return pd.DataFrame([data])
        if ext == "xlsx":
            return pd.read_excel(f)
    return None


def map_columns(df: pd.DataFrame, portal_id: str, portal_cfg: dict) -> pd.DataFrame:
    """Mapea columnas del portal al esquema normalizado. Documenta el mapeo."""
    col_map: dict[str, str] = {}

    # Mapeo heurístico por nombre de columna normalizado
    for col in df.columns:
        nc = normalize_text(str(col))
        if re.search(r"nombre|proyecto|titulo", nc):
            col_map.setdefault("nombre", col)
        elif re.search(r"cui|codigo.unico", nc):
            col_map.setdefault("cui", col)
        elif re.search(r"entidad|organismo|unidad.ejecutora", nc):
            col_map.setdefault("entidad", col)
        elif re.search(r"region|departamento", nc):
            col_map.setdefault("region", col)
        elif re.search(r"provincia", nc):
            col_map.setdefault("provincia", col)
        elif re.search(r"distrito", nc):
            col_map.setdefault("distrito", col)
        elif re.search(r"monto|inversion|valor", nc):
            col_map.setdefault("monto_raw", col)
        elif re.search(r"fase|estado|etapa", nc):
            col_map.setdefault("fase_estado", col)
        elif re.search(r"sector|funcion|funcion.onal", nc):
            col_map.setdefault("sector_funcion", col)
        elif re.search(r"nivel.gobierno|nivel|gobierno", nc):
            col_map.setdefault("nivel_gobierno", col)
        elif re.search(r"adjudicacion|fecha|anio|año", nc):
            col_map.setdefault("fecha_adjudicacion_estimada", col)
        elif re.search(r"url|link|enlace", nc):
            col_map.setdefault("fuente_url", col)
        elif re.search(r"tipo|iniciativa|modalidad", nc):
            col_map.setdefault("tipo_iniciativa", col)

    out = pd.DataFrame()
    out["nombre"] = df.get(col_map.get("nombre", ""), pd.Series(dtype=str))
    out["cui"] = df.get(col_map.get("cui", ""), pd.Series(dtype=str))
    out["entidad"] = df.get(col_map.get("entidad", ""), pd.Series(dtype=str))
    out["region"] = df.get(col_map.get("region", ""), pd.Series(dtype=str))
    out["provincia"] = df.get(col_map.get("provincia", ""), pd.Series(dtype=str))
    out["distrito"] = df.get(col_map.get("distrito", ""), pd.Series(dtype=str))
    out["fase_estado"] = df.get(col_map.get("fase_estado", ""), pd.Series(dtype=str))
    out["sector_funcion"] = df.get(col_map.get("sector_funcion", ""), pd.Series(dtype=str))
    out["nivel_gobierno"] = df.get(col_map.get("nivel_gobierno", ""), pd.Series(dtype=str))
    out["fecha_adjudicacion_estimada"] = df.get(col_map.get("fecha_adjudicacion_estimada", ""), pd.Series(dtype=str))
    out["fuente_url"] = df.get(col_map.get("fuente_url", ""), pd.Series(dtype=str))
    out["tipo_iniciativa"] = df.get(col_map.get("tipo_iniciativa", ""), pd.Series(dtype=str))

    # Montos
    monto_raw = df.get(col_map.get("monto_raw", ""), pd.Series(dtype=str))
    out["monto_soles"] = monto_raw.apply(parse_monto)
    out["monto_usd"] = out["monto_soles"].apply(
        lambda x: round(x / TIPO_CAMBIO, 2) if pd.notna(x) else None
    )

    out["portal"] = portal_id
    out["modalidad"] = portal_cfg.get("modalidad", "")
    out["fecha_extraccion"] = date.today().isoformat()

    # Detectar campos faltantes obligatorios
    required = ["nombre", "entidad", "region", "monto_soles", "fase_estado", "fuente_url"]
    out["campos_faltantes"] = out.apply(
        lambda row: ",".join(f for f in required if pd.isna(row.get(f)) or str(row.get(f, "")).strip() == ""),
        axis=1,
    )

    # Campos de clasificación vacíos (se llenan en clasificador)
    for col in ("fit_nivel", "fit_razon", "timing", "prioridad", "notas"):
        out[col] = None

    # Generamos id_interno provisional
    out["id_interno"] = (
        out["portal"] + "_" +
        out.index.astype(str).str.zfill(5)
    )

    # Asegurar todas las columnas del esquema
    for col in SCHEMA_COLS:
        if col not in out.columns:
            out[col] = None

    return out[SCHEMA_COLS]


def deduplicate(df: pd.DataFrame) -> tuple[pd.DataFrame, list[dict]]:
    """Deduplica por CUI o por nombre normalizado + entidad."""
    merged_log = []
    df = df.copy()
    df["_key"] = df.apply(
        lambda r: str(r.get("cui") or "").strip() or
                  normalize_text(str(r.get("nombre", ""))) + "|" + normalize_text(str(r.get("entidad", ""))),
        axis=1,
    )
    # Para duplicados, agrupamos los portales de origen
    groups = df.groupby("_key")
    deduped = []
    for key, group in groups:
        if len(group) > 1:
            primary = group.iloc[0].copy()
            portales = ",".join(group["portal"].unique())
            primary["portal"] = portales
            merged_log.append({
                "key": key,
                "n": len(group),
                "portales": portales,
                "nombre": primary["nombre"],
            })
            deduped.append(primary)
        else:
            deduped.append(group.iloc[0])

    result = pd.DataFrame(deduped).drop(columns=["_key"], errors="ignore")
    return result, merged_log


def normalize_all(run_date: date) -> pd.DataFrame:
    config = yaml.safe_load(Path("config/filters.yaml").read_text())
    portales_cfg = config["portales"]
    frames = []

    for pid, pcfg in portales_cfg.items():
        df_raw = load_raw(pid, run_date)
        if df_raw is None or df_raw.empty:
            print(f"[{pid}] Sin datos crudos disponibles")
            continue
        df_norm = map_columns(df_raw, pid, pcfg)
        frames.append(df_norm)
        print(f"[{pid}] Normalizado: {len(df_norm)} filas")

    if not frames:
        print("No se encontraron datos crudos para ningún portal.")
        return pd.DataFrame(columns=SCHEMA_COLS)

    df_all = pd.concat(frames, ignore_index=True)
    df_dedup, merge_log = deduplicate(df_all)

    print(f"\nTotal antes de dedup: {len(df_all)}, después: {len(df_dedup)}, fusionados: {len(merge_log)}")
    if merge_log:
        print("Duplicados fusionados:")
        for m in merge_log:
            print(f"  {m['nombre'][:60]} ({m['portales']}) — {m['n']} ocurrencias")

    # Muestra de verificación de montos
    sample = df_dedup[df_dedup["monto_soles"].notna()].head(10)[["nombre", "monto_soles", "monto_usd", "portal"]]
    print("\nMuestra de montos (verificación manual):")
    print(sample.to_string(index=False))

    # Guardar parquet del día
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    out_file = PROCESSED_DIR / f"oportunidades_{run_date.isoformat()}.parquet"
    df_dedup.to_parquet(out_file, index=False)
    print(f"\nGuardado: {out_file}")

    # Anexar al histórico
    hist_file = PROCESSED_DIR / "oportunidades.parquet"
    if hist_file.exists():
        hist = pd.read_parquet(hist_file)
        # Eliminar registros de la misma fecha para reemplazarlos
        hist = hist[hist["fecha_extraccion"] != run_date.isoformat()]
        combined = pd.concat([hist, df_dedup], ignore_index=True)
    else:
        combined = df_dedup
    combined.to_parquet(hist_file, index=False)
    print(f"Histórico actualizado: {hist_file} ({len(combined)} registros totales)")

    # Documentar mapeo de columnas
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    doc_file = DOCS_DIR / "mapeo_columnas.md"
    doc_file.write_text(
        "# Mapeo de columnas por portal\n\n"
        "Este archivo se genera automáticamente en cada corrida.\n\n"
        "| Portal | Columnas originales |\n|--------|--------------------|\n" +
        "".join(
            f"| {pid} | {', '.join(load_raw(pid, run_date).columns.tolist() if load_raw(pid, run_date) is not None else ['N/A'])} |\n"
            for pid in portales_cfg
        )
    )

    return df_dedup
