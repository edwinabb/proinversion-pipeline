"""Punto de entrada: python -m src.run [--date today|YYYY-MM-DD] [--portal P1] [--dry-run]"""

import argparse
import asyncio
from datetime import date


def parse_args():
    p = argparse.ArgumentParser(description="Pipeline ProInversión → YOFC")
    p.add_argument("--date", default="today", help="Fecha de la corrida (today o YYYY-MM-DD)")
    p.add_argument("--portal", nargs="*", help="Portales a procesar (P1 P2 …). Default: todos")
    p.add_argument("--dry-run", action="store_true", help="Solo extrae el primer portal y muestra 5 registros")
    p.add_argument("--skip-scrape", action="store_true", help="Salta el scraping y usa datos crudos existentes")
    return p.parse_args()


def resolve_date(d: str) -> date:
    return date.today() if d == "today" else date.fromisoformat(d)


def main():
    args = parse_args()
    run_date = resolve_date(args.date)
    print(f"=== Pipeline ProInversión — {run_date.isoformat()} ===\n")

    manifests = {}

    # Fase 1: Scraping
    if not args.skip_scrape:
        from src.scraper import run_scraper
        manifests = asyncio.run(run_scraper(
            portales=args.portal,
            run_date=run_date,
            dry_run=args.dry_run,
        ))
    else:
        print("[SKIP] Scraping omitido. Usando datos crudos existentes.")

    # Fase 2: Normalización
    from src.normalizer import normalize_all
    df = normalize_all(run_date)

    if df.empty:
        print("\nNo hay datos normalizados. Corrida terminada.")
        return

    if args.dry_run:
        print("\n=== DRY RUN: muestra de 5 registros ===")
        print(df[["nombre", "entidad", "region", "monto_soles", "monto_usd", "fase_estado"]].head(5).to_string(index=False))
        print("\nDRY RUN completado. Esperando OK para corrida completa.")
        return

    # Fase 3: Clasificación
    from src.classifier import classify_all
    df, dubious, seed_results = classify_all(df)

    # Guardar parquet clasificado
    from pathlib import Path
    out_parquet = Path("data/processed") / f"oportunidades_{run_date.isoformat()}.parquet"
    df.to_parquet(out_parquet, index=False)

    # Fase 4: Reporte
    from src.reporter import generate_excel, generate_report
    generate_excel(df, run_date, seed_results, manifests)
    generate_report(df, run_date, seed_results, manifests, dubious)

    # Resumen final (5 líneas)
    n_total = len(df)
    n_5m = int((df["monto_usd"] > 5_000_000).sum()) if "monto_usd" in df.columns else "N/D"
    n_a = int((df["prioridad"] == "A").sum())
    n_b = int((df["prioridad"] == "B").sum())
    n_c = int((df["prioridad"] == "C").sum())
    seeds_missing = [s["semilla"] for s in seed_results if not s["encontrada"]]
    errores = [pid for pid, m in manifests.items() if m.get("errores")]

    print(f"""
=== RESUMEN ===
Total extraído:     {n_total} registros
Total > US$ 5M:     {n_5m}
Prioridad A/B/C:    {n_a} / {n_b} / {n_c}
Semillas faltantes: {', '.join(seeds_missing) if seeds_missing else 'Ninguna'}
Problemas:          {', '.join(errores) if errores else 'Sin errores'}
""")


if __name__ == "__main__":
    main()
