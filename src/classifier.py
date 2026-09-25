"""Asigna fit_nivel, fit_razon, timing y prioridad a cada oportunidad."""

import re
import unicodedata
from pathlib import Path

import pandas as pd
import yaml


def _norm(s) -> str:
    if not isinstance(s, str):
        return ""
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return s.lower().strip()


def load_keywords(config: dict) -> tuple[list, list, list]:
    clf = config["clasificacion"]
    return (
        [_norm(k) for k in clf["fit_core_keywords"]],
        [_norm(k) for k in clf["fit_adyacente_keywords"]],
        [_norm(k) for k in clf["fit_periferico_keywords"]],
    )


def classify_fit(row: pd.Series, core_kw: list, adj_kw: list, peri_kw: list) -> tuple[int, str]:
    text = " ".join([
        _norm(str(row.get("nombre", ""))),
        _norm(str(row.get("sector_funcion", ""))),
        _norm(str(row.get("tipo_iniciativa", ""))),
        _norm(str(row.get("notas", ""))),
    ])

    for kw in core_kw:
        if kw in text:
            return 3, f"contiene '{kw}'"
    for kw in adj_kw:
        if kw in text:
            return 2, f"contiene '{kw}'"
    for kw in peri_kw:
        if kw in text:
            return 1, f"componente TI periférico: '{kw}'"

    # Sector explícito
    sector = _norm(str(row.get("sector_funcion", "")))
    if re.search(r"telecomunicacion|comunicacion|tecnologia", sector):
        return 3, f"sector={sector}"
    if re.search(r"orden publico|seguridad ciudadana|justicia|interior", sector):
        return 2, f"sector seguridad/justicia: posible componente monitoreo"

    return 0, "sin palabras clave relevantes"


def classify_timing(fase: str, timing_map: dict) -> str:
    fn = _norm(str(fase))
    for timing, keywords in timing_map.items():
        for kw in keywords:
            if _norm(kw) in fn:
                return timing
    return "MEDIO"  # fallback conservador


def classify_priority(row: pd.Series) -> str:
    fit = row.get("fit_nivel", 0) or 0
    timing = row.get("timing", "")
    monto_usd = row.get("monto_usd")

    if fit == 0:
        return None
    if isinstance(monto_usd, float) and monto_usd <= 5_000_000:
        return None

    monto_conocido = isinstance(monto_usd, float) and monto_usd > 5_000_000
    monto_desconocido = not isinstance(monto_usd, (int, float)) or pd.isna(monto_usd)

    if fit >= 2 and monto_conocido and timing in ("TEMPRANO", "URGENTE"):
        return "A"
    if fit >= 2 and monto_conocido and timing == "MEDIO":
        return "B"
    if fit == 3 and monto_desconocido:
        return "B"
    if fit >= 1:
        return "C"
    return None


def validate_seeds(df: pd.DataFrame, seeds: list) -> list[dict]:
    results = []
    for seed in seeds:
        nombre_seed = _norm(seed["nombre"])
        match = df[df["nombre"].apply(lambda x: nombre_seed in _norm(str(x)))]
        found = not match.empty
        results.append({
            "semilla": seed["nombre"],
            "encontrada": found,
            "prioridad": match.iloc[0]["prioridad"] if found else None,
            "n_matches": len(match),
        })
    return results


def classify_all(df: pd.DataFrame) -> tuple[pd.DataFrame, list[dict], list[dict]]:
    config = yaml.safe_load(Path("config/filters.yaml").read_text())
    core_kw, adj_kw, peri_kw = load_keywords(config)
    timing_map = config["clasificacion"]["timing_map"]
    seeds = config["semillas_validacion"]

    df = df.copy()
    dubious = []

    fit_results = df.apply(lambda r: classify_fit(r, core_kw, adj_kw, peri_kw), axis=1)
    df["fit_nivel"] = fit_results.apply(lambda x: x[0])
    df["fit_razon"] = fit_results.apply(lambda x: x[1])

    df["timing"] = df["fase_estado"].apply(lambda f: classify_timing(f, timing_map))
    df["prioridad"] = df.apply(classify_priority, axis=1)

    # Detectar casos dudosos: fit == 0 con sector telecom/seguridad, o fit distinto del sector
    for idx, row in df.iterrows():
        sector = _norm(str(row.get("sector_funcion", "")))
        if row["fit_nivel"] == 0 and re.search(r"telecomunicacion|comunicacion|seguridad|interior", sector):
            dubious.append({"idx": idx, "nombre": row["nombre"], "sector": row["sector_funcion"], "fit_razon": row["fit_razon"]})
        elif row["fit_nivel"] == 1 and re.search(r"telecomunicacion|comunicacion", sector):
            dubious.append({"idx": idx, "nombre": row["nombre"], "sector": row["sector_funcion"], "fit_razon": row["fit_razon"]})

    dubious = dubious[:10]

    seed_results = validate_seeds(df, seeds)

    print(f"\nClasificación:")
    for p in ("A", "B", "C", None):
        n = (df["prioridad"] == p).sum() if p else df["prioridad"].isna().sum()
        label = p or "EXCLUIR"
        print(f"  Prioridad {label}: {n}")

    print("\nSemillas de validación:")
    for s in seed_results:
        status = "OK" if s["encontrada"] else "NO ENCONTRADA"
        print(f"  [{status}] {s['semilla']} → prioridad {s['prioridad']}")

    if dubious:
        print(f"\n{len(dubious)} casos dudosos para revisión humana:")
        for d in dubious:
            print(f"  {d['nombre'][:60]} | sector: {d['sector']} | razon: {d['fit_razon']}")

    return df, dubious, seed_results
