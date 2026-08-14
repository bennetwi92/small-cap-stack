"""Step 2 — decompose `passed` into its component gates.

`failing_gates` is a delimited list of the shape gates a row failed. `passed` is
"failed nothing". This asks, for each named gate, whether *failing* it is good or bad — i.e.
whether the gate is worth having at all.
"""

from __future__ import annotations

import json

import lab
import polars as pl


def gate_names(p: pl.DataFrame) -> list[str]:
    names: set[str] = set()
    for s in p["failing_gates"].drop_nulls():
        for tok in str(s).replace(";", ",").split(","):
            tok = tok.strip()
            if tok:
                names.add(tok)
    return sorted(names)


def main() -> None:
    p = lab.no_holdout(lab.panel())
    print("sample failing_gates values:")
    print(p["failing_gates"].value_counts(sort=True).head(15))

    names = gate_names(p)
    print(f"\n{len(names)} distinct gate tokens: {names}\n")

    base_g = float(p["r"].mean())
    base_n = float(p["net_r"].mean())
    print(f"pool: n={p.height} gross {base_g:+.3f} net {base_n:+.3f}\n")

    rows = []
    for g in names:
        fail = p.filter(pl.col("failing_gates").str.contains(g, literal=True))
        ok = p.filter(~pl.col("failing_gates").str.contains(g, literal=True))
        if fail.height < 30 or ok.height < 30:
            continue
        rows.append(
            {
                "gate": g,
                "n_fail": fail.height,
                "gross_fail": round(float(fail["r"].mean()), 3),
                "gross_pass": round(float(ok["r"].mean()), 3),
                "edge_of_gate": round(float(ok["r"].mean()) - float(fail["r"].mean()), 3),
                "n_pass": ok.height,
                "dev_edge": round(
                    float(ok.filter(pl.col("split") == "dev")["r"].mean())
                    - float(fail.filter(pl.col("split") == "dev")["r"].mean()),
                    3,
                ),
                "val_edge": round(
                    float(ok.filter(pl.col("split") == "val")["r"].mean())
                    - float(fail.filter(pl.col("split") == "val")["r"].mean()),
                    3,
                ),
            }
        )
    t = pl.DataFrame(rows).sort("edge_of_gate", descending=True)
    print("Gate value: positive edge_of_gate = the gate helps (rows that fail it are worse)")
    with pl.Config(tbl_rows=60, tbl_cols=20, tbl_width_chars=200):
        print(t)

    # number of gates failed
    print("\nby count of failing gates:")
    q = p.with_columns(
        pl.when(pl.col("failing_gates").is_null() | (pl.col("failing_gates").str.len_chars() == 0))
        .then(0)
        .otherwise(pl.col("failing_gates").str.count_matches(",") + 1)
        .alias("n_gates_failed")
    )
    with pl.Config(tbl_rows=30):
        print(
            q.group_by("n_gates_failed")
            .agg(
                pl.len().alias("n"),
                pl.col("r").mean().round(3).alias("gross"),
                pl.col("net_r").mean().round(3).alias("net"),
                (pl.col("r") > 0).mean().round(3).alias("win"),
            )
            .sort("n_gates_failed")
        )

    lab.OUT.mkdir(parents=True, exist_ok=True)
    (lab.OUT / "step2_gates.json").write_text(json.dumps(t.to_dicts(), indent=1))


if __name__ == "__main__":
    main()
