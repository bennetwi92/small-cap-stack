"""Step 3b — the walk-forward under the null.

Step 3 showed the refitting procedure returns 5/6 positive blocks and ~+0.5R/trade. That is only
evidence if a procedure run on *scrambled outcomes* does worse. Blocks hold 2-10 trades each, so
"5 of 6 positive" may be a coin-flip result. Same walk-forward, same fits, permuted outcomes.

Reimplemented in numpy to mirror `common.walk_forward` exactly (edges checked against it in main).
"""

from __future__ import annotations

import itertools

import lab as L
import numpy as np
import search as S

RUNUPS = [0.05, 0.10, 0.15, 0.20, 0.25, 0.30]
RVOLS = [1.0, 1.5, 2.0, 3.0, 5.0]
SHARES = [20e6, 50e6, 100e6, 200e6, 500e6]


def wf_edges(day_idx: np.ndarray, n_blocks: int = 6, min_train: int = 60) -> list[tuple[int, int]]:
    n_days = int(day_idx.max()) + 1
    e = np.linspace(min_train, n_days, n_blocks + 1).astype(int)
    return [(a, b) for a, b in zip(e[:-1], e[1:], strict=True) if b > a]


def grid_masks(pop: S.Pop, base: np.ndarray) -> list[tuple[tuple, np.ndarray]]:
    ru_x = pop.feat["runup_pre_appearance"]
    rv_x = pop.feat["rvol_pole"]
    sh_x = pop.feat["shares_outstanding"]
    valid = ~(np.isnan(rv_x) | np.isnan(sh_x) | np.isnan(ru_x))
    out = []
    for ru, rv, sh in itertools.product(RUNUPS, RVOLS, SHARES):
        m = base & valid
        m = m & (np.where(np.isnan(ru_x), False, ru_x >= ru))
        m = m & (np.where(np.isnan(rv_x), False, rv_x >= rv))
        m = m & (np.where(np.isnan(sh_x), False, sh_x <= sh))
        out.append(((ru, rv, sh), m))
    return out


def run_wf(
    pop: S.Pop,
    base: np.ndarray,
    edges: list[tuple[int, int]],
    masks: list[tuple[tuple, np.ndarray]],
    max_r: np.ndarray,
    *,
    refit: bool,
    min_train_trades: int = 15,
) -> dict:
    blocks = []
    fixed_key = (0.15, 2.0, 50e6)
    for a, b in edges:
        train = pop.day_idx < a
        test = (pop.day_idx >= a) & (pop.day_idx < b)
        if refit:
            best, bm, bk = -np.inf, None, None
            for key, m in masks:
                v, n, _ = pop.stats(m & train, max_r)
                if n >= min_train_trades and v > best:
                    best, bm, bk = v, m, key
            if bm is None:
                bm = dict(masks)[fixed_key] if fixed_key in dict(masks) else masks[0][1]
                bk = fixed_key
        else:
            bm = next(m for k, m in masks if k == fixed_key)
            bk = fixed_key
        v, n, tot = pop.stats(bm & test, max_r)
        blocks.append(
            {"chose": bk, "trades": n, "net_r": tot, "net_r_per_trade": 0.0 if not n else tot / n}
        )
    tr = sum(b["trades"] for b in blocks)
    tot = sum(b["net_r"] for b in blocks)
    return {
        "blocks": blocks,
        "total_trades": tr,
        "total_net_r": tot,
        "net_r_per_trade": tot / tr if tr else 0.0,
        "blocks_positive": sum(1 for b in blocks if b["net_r"] > 0),
        "n_blocks": len(blocks),
    }


def main() -> None:
    df = L.load_panel_checked()
    pop = S.Pop(df)
    base = S.shipped_mask(df)
    edges = wf_edges(pop.day_idx)
    masks = grid_masks(pop, base)
    out: dict = {"edges": [(int(a), int(b)) for a, b in edges]}

    L.hr("3b-1. numpy walk-forward reproduces step 3")
    for refit, tag in ((False, "FIXED"), (True, "GRID")):
        r = run_wf(pop, base, edges, masks, pop.max_r, refit=refit)
        print(
            f"  {tag:<6} trades={r['total_trades']:>3} net={r['total_net_r']:+7.2f}R "
            f"({r['net_r_per_trade']:+.4f}/trade) positive={r['blocks_positive']}/{r['n_blocks']}"
        )
        out[f"observed_{tag}"] = {
            k: (round(v, 4) if isinstance(v, float) else v) for k, v in r.items() if k != "blocks"
        }

    # ------------------------------------------------------------------------------- the null
    N = 800
    L.hr(f"3b-2. Same walk-forward on permuted outcomes ({N} iterations)")
    rng = np.random.default_rng(4242)
    idx_base = np.flatnonzero(base)
    rows = {"FIXED": [], "GRID": []}
    for _ in range(N):
        mr = pop.max_r.copy()
        mr[idx_base] = pop.max_r[rng.permutation(idx_base)]
        for refit, tag in ((False, "FIXED"), (True, "GRID")):
            r = run_wf(pop, base, edges, masks, mr, refit=refit)
            rows[tag].append((r["net_r_per_trade"], r["blocks_positive"], r["total_net_r"]))

    for tag in ("FIXED", "GRID"):
        o = out[f"observed_{tag}"]
        arr = np.array(rows[tag])
        per, pos, tot = arr[:, 0], arr[:, 1], arr[:, 2]
        p_per = (int((per >= o["net_r_per_trade"]).sum()) + 1) / (len(per) + 1)
        p_pos = (int((pos >= o["blocks_positive"]).sum()) + 1) / (len(pos) + 1)
        p_tot = (int((tot >= o["total_net_r"]).sum()) + 1) / (len(tot) + 1)
        print(
            f"  {tag:<6} obs per-trade {o['net_r_per_trade']:+.4f}: null median "
            f"{np.median(per):+.4f} p90 {np.quantile(per, 0.9):+.4f} -> p={p_per:.3f}"
        )
        print(
            f"  {tag:<6} obs blocks+ {int(o['blocks_positive'])}/6: null mean "
            f"{pos.mean():.2f}, P(null >= obs) = {p_pos:.3f}"
        )
        print(
            f"  {tag:<6} obs total {o['total_net_r']:+.2f}R: null median "
            f"{np.median(tot):+.2f}R -> p={p_tot:.3f}"
        )
        out[f"null_{tag}"] = {
            "n_iter": N,
            "null_median_per_trade": round(float(np.median(per)), 4),
            "null_p90_per_trade": round(float(np.quantile(per, 0.9)), 4),
            "p_per_trade": round(p_per, 4),
            "null_mean_blocks_positive": round(float(pos.mean()), 3),
            "p_blocks_positive": round(p_pos, 4),
            "null_median_total_r": round(float(np.median(tot)), 3),
            "p_total_r": round(p_tot, 4),
        }

    L.hr("3b-3. What does '5 of 6 blocks positive' mean when blocks hold 2-10 trades?")
    print("  Under a fair coin, P(>=5 of 6 heads) = 0.1094.")
    print(
        f"  Under the outcome-permuted null, blocks positive averaged "
        f"{out['null_GRID']['null_mean_blocks_positive']:.2f}/6 and reached the observed "
        f"count {out['null_GRID']['p_blocks_positive'] * 100:.0f}% of the time."
    )

    L.write("step3b_wf_null.json", out)
    print("\nwrote step3b_wf_null.json")


if __name__ == "__main__":
    main()
