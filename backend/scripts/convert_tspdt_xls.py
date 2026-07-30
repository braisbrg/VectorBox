"""Convert a TSPDT starting-list .xls into the from_file TSV format.

Usage (inside the backend container):
    pip install xlrd   # one-off dep, not in requirements
    python scripts/convert_tspdt_xls.py scripts/data/StartingList_21stCentury.xls scripts/data/tspdt_21st.tsv

Output columns: Pos / Rank / Title / Director / Year / Country / Mins / IMDb.
Rows are sorted by best (most recent non-zero) poll rank, so seeding with
--limit N ingests the top-N most-acclaimed films first. The IMDb column comes
from the embedded cell hyperlinks and lets seed_db resolve via TMDB /find
(exact, no search heuristics).
"""
import re
import sys

import pandas as pd
import xlrd


def main(src: str, out: str) -> None:
    book = xlrd.open_workbook(src, formatting_info=True)
    sheet = book.sheet_by_index(0)
    links = {}
    for (r, _c), link in getattr(sheet, "hyperlink_map", {}).items():
        m = re.search(r"tt\d+", getattr(link, "url_or_path", None) or "")
        if m:
            links[r] = m.group()

    df = pd.read_excel(src, sheet_name=0)
    rank_cols = sorted((c for c in df.columns if isinstance(c, int)), reverse=True)

    clean = lambda v: str(v if pd.notna(v) else "").replace("\t", " ").strip()
    rows = []
    for i, rec in df.iterrows():
        title, year = clean(rec.get("Title")), clean(rec.get("Year"))
        if not title or not re.search(r"\d{4}", year):
            continue
        rank = 99999
        for c in rank_cols:
            v = rec.get(c)
            if pd.notna(v) and int(v) > 0:
                rank = int(v)
                break
        rows.append((rank, title, clean(rec.get("Director(s)")), year,
                     clean(rec.get("Country")), clean(rec.get("Length")),
                     links.get(i + 1, "")))  # sheet row = df index + header row

    rows.sort(key=lambda r: r[0])
    with open(out, "w", encoding="utf-8") as f:
        f.write("Pos\tRank\tTitle\tDirector\tYear\tCountry\tMins\tIMDb\n")
        for pos, r in enumerate(rows, 1):
            f.write(f"{pos}\t" + "\t".join(str(x) for x in r) + "\n")

    with_imdb = sum(1 for r in rows if r[6])
    print(f"{len(rows)} rows written to {out} | {with_imdb} with IMDb id "
          f"({100 * with_imdb // len(rows)}%) | rank<=1000: {sum(1 for r in rows if r[0] <= 1000)} | "
          f"<=3000: {sum(1 for r in rows if r[0] <= 3000)} | unranked: {sum(1 for r in rows if r[0] == 99999)}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
