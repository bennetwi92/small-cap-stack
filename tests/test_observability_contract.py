"""The metrics ↔ dashboards ↔ alerts contract (#688).

`docs/` has the same problem in a different costume: nothing links a page's HTML to the module that
reaches into it by id, so `tests/test_dashboard_dom.py` makes the permanent form of that mismatch
unmergeable (#406). Grafana is worse, because the two halves are not even in the same *system* —
a dashboard lives in Grafana Cloud, the metric lives in `monitoring.py`, and nothing anywhere fails
when they disagree. A renamed metric produces a panel that draws **No data**, which is
indistinguishable from "the thing being measured is quiet", which is the exact reading you want to
trust at 04:00.

So the dashboards and the alert rules are committed here and this file binds them, in both
directions:

- **Every `scs_*` a panel or a rule names must exist** in the app's registry (or be one of the
  handful produced outside the process, listed and justified below).
- **Every `scs_*` the app defines must be named** by a panel or a rule. A metric nobody looks at is
  series we pay for forever and a maintenance cost with no reader; if it is genuinely not worth
  watching, delete it rather than keeping it warm.

The alert rules are parsed by hand rather than with PyYAML, which is not a dependency of this
project and is not worth becoming one — the same call `tests/test_deployment.py` makes about the
workflow files. Every parser here is pinned by a non-vacuous test, because a regex that matches
nothing turns every check built on it green.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from small_cap_stack.monitoring import (
    CAPTURE_STAGES,
    DASHBOARD_ARTIFACTS,
    IBKR_REQUEST_KINDS,
    TICK_PHASES,
    metric_names,
)
from tests.support import settings

ROOT = Path(__file__).resolve().parents[1]
DASHBOARD_DIR = ROOT / "deploy" / "grafana" / "dashboards"
RULES = ROOT / "deploy" / "grafana" / "alerts" / "scs-rules.yaml"
ALLOY = ROOT / "deploy" / "alloy" / "config.alloy"
FRESHNESS = ROOT / "scripts" / "dashboard-freshness.sh"
SRC = ROOT / "src" / "small_cap_stack"

#: `scs_*` series that are real but do NOT come from the app's registry, so the existence check
#: cannot find them there. Exactly one producer today: `scripts/dashboard-freshness.sh`, which
#: writes node_exporter textfile metrics. It has to be outside the process — it measures whether
#: the app's output ever reached the branch the browser reads, and the app is not involved in that
#: chain at all (a GitHub cron is). `test_external_metrics_have_a_producer` pins the producer, so
#: this is an exemption from *where* a metric lives, never from whether it exists.
EXTERNAL_METRICS = frozenset({"scs_published_status_age_seconds", "scs_published_probe_success"})

#: Metrics deliberately defined and deliberately not graphed. Empty, and meant to stay that way —
#: it exists so that adding one is a visible, reviewable decision rather than an omission.
UNWATCHED_METRICS: frozenset[str] = frozenset()

_SUFFIX = re.compile(r"_(bucket|count|sum|total|created)$")
_METRIC = re.compile(r"\bscs_[a-z0-9_]+")


def _base(name: str) -> str:
    """Strip Prometheus sample suffixes down to the metric name.

    `Counter("scs_scan_ticks_total")` registers under `scs_scan_ticks` and exposes
    `scs_scan_ticks_total`; a histogram exposes `_bucket`/`_count`/`_sum`. PromQL names samples,
    the registry names metrics, and comparing them without normalising both sides is how this test
    would pass while proving nothing.
    """
    while _SUFFIX.search(name):
        name = _SUFFIX.sub("", name)
    return name


def _dashboards() -> list[tuple[str, dict[str, Any]]]:
    return [(p.name, json.loads(p.read_text())) for p in sorted(DASHBOARD_DIR.glob("*.json"))]


def _dashboard_exprs() -> list[tuple[str, str]]:
    """(dashboard, expr) for every PromQL expression in every dashboard.

    Only `expr` keys, never every string in the file: the panel descriptions quote metric names in
    prose deliberately, and folding those in would make the test assert that the documentation
    compiles rather than that the queries do.
    """
    found: list[tuple[str, str]] = []

    def walk(name: str, node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "expr" and isinstance(value, str):
                    found.append((name, value))
                walk(name, value)
        elif isinstance(node, list):
            for value in node:
                walk(name, value)

    for name, doc in _dashboards():
        walk(name, doc)
    return found


def _alert_rules() -> list[dict[str, str]]:
    """Every alert in the rule file, as {alert, expr, for, severity, summary, description}.

    Hand-parsed. The shape it depends on — a two-space-indented `- alert:` opening each rule, its
    keys indented under it — is pinned by `test_the_rule_parse_is_not_vacuous`, so a reformat that
    breaks the parse fails loudly instead of making every check below vacuous.
    """
    rules: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    section = ""
    for raw in RULES.read_text().splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if stripped.startswith("#") or not stripped:
            continue
        if m := re.match(r"^\s+- alert:\s*(\S+)", line):
            current = {"alert": m.group(1)}
            rules.append(current)
            section = ""
            continue
        if current is None:
            continue
        if m := re.match(r"^\s+expr:\s*(.+)$", line):
            current["expr"] = m.group(1).strip()
        elif m := re.match(r"^\s+for:\s*(\S+)", line):
            current["for"] = m.group(1)
        elif "severity:" in stripped:
            current["severity"] = stripped.split("severity:")[1].strip(" }")
        elif m := re.match(r"^\s+(summary|description):\s*(.*)$", line):
            section = m.group(1)
            current[section] = m.group(2).strip()
        elif section:
            current[section] = (current.get(section, "") + " " + stripped).strip()
    return rules


def _rule_exprs() -> list[str]:
    return [r["expr"] for r in _alert_rules() if "expr" in r]


def _referenced_metrics() -> set[str]:
    names: set[str] = set()
    for _, expr in _dashboard_exprs():
        names.update(_METRIC.findall(expr))
    for expr in _rule_exprs():
        names.update(_METRIC.findall(expr))
    return {_base(n) for n in names}


def _defined_metrics() -> set[str]:
    return {n for n in metric_names() if n.startswith("scs_")}


# ------------------------------------------------------------------ the parsers themselves


def test_the_dashboard_parse_is_not_vacuous() -> None:
    """Compared against the files on disk, not a `>= N` floor.

    A floor tolerates a whole dashboard being deleted silently and then fails on the *second*
    deletion with a message that reads like a parser break — the objection
    `test_every_workflow_contributes_to_the_job_inventory` raises about exactly this shape.
    """
    files = {p.name for p in DASHBOARD_DIR.glob("*.json")}
    assert files, "no dashboards found — has deploy/grafana/dashboards moved?"
    parsed = {name for name, _ in _dashboard_exprs()}
    assert parsed == files, (
        f"these dashboards contributed no PromQL at all, so every check below passes vacuously "
        f"for them: {sorted(files - parsed)}"
    )


def test_the_rule_parse_is_not_vacuous() -> None:
    """The hand-written parser must find every rule the file declares."""
    declared = len(re.findall(r"^\s+- alert:", RULES.read_text(), re.M))
    rules = _alert_rules()
    assert declared > 10, "the rule file looks truncated"
    assert len(rules) == declared, (
        f"the parser found {len(rules)} of {declared} alerts — it has lost its place, and every "
        f"completeness check below is now only checking the ones it happened to see"
    )
    assert all(r.get("expr") for r in rules), (
        f"an alert parsed with no expr: {[r['alert'] for r in rules if not r.get('expr')]}"
    )


# ------------------------------------------------------------------ the contract, both ways


def test_every_metric_a_panel_or_rule_names_actually_exists() -> None:
    """The direction that produces a silently empty panel.

    Grafana does not fail on an unknown metric — it draws **No data**, which reads exactly like
    "the thing being measured is quiet". That is the worst possible failure for a monitoring
    system: it is indistinguishable from health, on the screen you look at to decide whether
    something is healthy.
    """
    missing = _referenced_metrics() - _defined_metrics() - EXTERNAL_METRICS
    assert not missing, (
        "these are queried by a dashboard or an alert but no longer exist in the app's registry, "
        f"so their panels draw 'No data' forever: {sorted(missing)}\n"
        "Either rename them back, update deploy/grafana/, or add them to EXTERNAL_METRICS with a "
        "producer."
    )


def test_every_metric_the_app_defines_is_watched_somewhere() -> None:
    """The direction that produces cost with no reader.

    Grafana Cloud's free tier bills 10k active series and this box is already shipping node and
    cadvisor. A metric nobody graphs and nobody alerts on is series spent forever plus a wiring
    site to keep correct — so it is either worth watching or worth deleting, and this makes that a
    decision rather than an oversight.
    """
    unwatched = _defined_metrics() - _referenced_metrics() - UNWATCHED_METRICS
    assert not unwatched, (
        "these metrics are defined and wired up but no panel or alert reads them: "
        f"{sorted(unwatched)}\n"
        "Add a panel in deploy/grafana/dashboards/, an alert in deploy/grafana/alerts/, or delete "
        "the metric. If it is genuinely worth keeping unwatched, say so in UNWATCHED_METRICS."
    )


def test_external_metrics_have_a_producer() -> None:
    """EXTERNAL_METRICS is an exemption from *where* a metric lives, not from whether it exists."""
    script = FRESHNESS.read_text()
    for name in EXTERNAL_METRICS:
        assert name in script, (
            f"{name} is exempted from the registry check but nothing produces it — "
            f"{FRESHNESS.name} is supposed to be its source"
        )


# ------------------------------------------------------------------ dashboards are importable


def test_dashboards_bind_no_datasource_uid() -> None:
    """Every query must go through the `${ds}` template variable.

    A dashboard exported from Grafana carries the *source stack's* datasource uid baked into every
    panel. Import it into another stack — or re-create the datasource after a stack rebuild — and
    every panel silently points at a datasource that no longer exists. The template variable is
    what makes these files portable, which is the point of committing them at all.
    """
    offenders: list[str] = []
    for name, doc in _dashboards():
        for match in re.finditer(r'"uid":\s*"([^"]+)"', json.dumps(doc)):
            uid = match.group(1)
            if uid not in ("${ds}", doc["uid"]):
                offenders.append(f"{name}: {uid}")
    assert not offenders, (
        f"hard-coded datasource uids — re-export with the `ds` variable selected: {offenders}"
    )


def test_dashboard_uids_are_unique_and_stable() -> None:
    """The uid is the dashboard's identity across imports; a collision silently overwrites."""
    uids = [doc["uid"] for _, doc in _dashboards()]
    assert len(uids) == len(set(uids)), f"duplicate dashboard uids: {uids}"
    assert all(uid.startswith("scs-") for uid in uids), uids


# ------------------------------------------------------------------ the alerts are usable


@pytest.mark.parametrize(("field", "floor"), [("summary", 20), ("description", 80)])
def test_every_alert_says_what_is_wrong_and_what_to_do(field: str, floor: int) -> None:
    """An alert without a description is a pager that says "SCSThing" at 04:00.

    Two different floors, because the two fields have opposite jobs. The **summary** is the
    notification line and wants to be short — anything past a sentence is unreadable on a phone.
    The **description** is what you read once you are awake, and it is where the "what do I do"
    lives; a length floor is a crude proxy for that, but it is the one that rejects
    `description: see dashboard`, which satisfies mere presence and helps nobody.
    """
    thin = [r["alert"] for r in _alert_rules() if len(r.get(field, "")) < floor]
    assert not thin, f"these alerts have no usable `{field}` (< {floor} chars): {thin}"


def test_every_alert_carries_a_known_severity() -> None:
    known = {"critical", "warning", "info"}
    bad = [
        (r["alert"], r.get("severity")) for r in _alert_rules() if r.get("severity") not in known
    ]
    assert not bad, f"alerts with a missing or unknown severity: {bad}"


def test_in_session_symptoms_are_gated_on_the_session() -> None:
    """The rule that keeps this alert set from being muted, checked instead of remembered.

    Every symptom below is *correct* outside a session: the Gateway is restarted nightly at 23:45
    by design, and the scanner returns nothing at 21:00 on a Sunday. An alert that fires then gets
    muted, and a muted alert is worse than no alert because it looks like coverage. So any rule
    built on these three series must multiply by both session gauges.
    """
    session_scoped = ("scs_ibkr_connected", "scs_ibkr_data_farm_ok", "scs_scan_candidates")
    ungated = []
    for rule in _alert_rules():
        expr = rule.get("expr", "")
        if not any(metric in expr for metric in session_scoped):
            continue
        if not ("scs_in_scan_window" in expr and "scs_trading_day" in expr):
            ungated.append(rule["alert"])
    assert not ungated, (
        "these alert on a symptom that is normal outside market hours without gating on "
        f"scs_in_scan_window AND scs_trading_day: {ungated}"
    )


def test_no_alert_fires_instantly_on_a_flapping_signal() -> None:
    """`for:` must exist wherever the underlying signal recovers on its own.

    The supervisor reconnects with backoff and `publish-dashboard` runs on a lagging 15-minute
    cron, so a rule with no `for:` on those signals pages on every routine recovery. The two
    exceptions are deliberate and named: a counter that has ALREADY accumulated (an OOM kill, a
    missed job) is not flapping — the event happened, and waiting to confirm it is just a delay.
    """
    instant_ok = {"increase(", "changes(", "delta(", "sum by"}
    offenders = []
    for rule in _alert_rules():
        if rule.get("for", "0m") != "0m":
            continue
        if any(token in rule.get("expr", "") for token in instant_ok):
            continue
        offenders.append(rule["alert"])
    assert not offenders, (
        f"these alert with `for: 0m` on a level signal that recovers by itself: {offenders}"
    )


# ------------------------------------------------------------ the collector agrees with the app


def test_alloy_scrapes_the_port_the_app_actually_serves() -> None:
    """The one number that has to match across a language boundary.

    `METRICS_PORT` is set in docker-compose, read by `config.py`, published by the app, and typed
    again by hand into the Alloy config. Nothing else connects those four, and getting it wrong
    produces a collector that scrapes nothing and a dashboard full of No data.
    """
    port = settings().metrics_port
    alloy = ALLOY.read_text()
    assert f"127.0.0.1:{port}" in alloy, (
        f"Alloy does not scrape 127.0.0.1:{port}, which is where `metrics_port` says the app "
        f"serves /metrics"
    )
    compose = (ROOT / "docker-compose.yml").read_text()
    assert f'METRICS_PORT: "{port}"' in compose, "compose and config.py disagree about the port"
    assert f"127.0.0.1:{port}:{port}" in compose, (
        "the metrics port must stay bound to localhost — it is an unauthenticated endpoint on a "
        "box with a live trading connection"
    )


def test_the_job_labels_dashboards_select_on_are_the_ones_alloy_sets() -> None:
    """`up{job="scs-app"}` is only true if Alloy names the job that."""
    alloy = ALLOY.read_text()
    declared = set(re.findall(r'job_name\s*=\s*"([\w-]+)"', alloy))
    assert declared, "no job_name found in the Alloy config — has the syntax changed?"
    selected = set()
    for _, expr in _dashboard_exprs():
        selected.update(re.findall(r'job\s*=~?\s*"([\w-]+)"', expr))
    for expr in _rule_exprs():
        selected.update(re.findall(r'job\s*=~?\s*"([\w-]+)"', expr))
    assert selected, "nothing selects on a job label — did the parser break?"
    assert selected <= declared, (
        f"dashboards/alerts select job labels Alloy never sets: {sorted(selected - declared)}"
    )


def test_alloy_reads_its_credentials_from_the_environment() -> None:
    """The config is public; the stack it writes to is not.

    This file lives in a **public** repo (decisions §D-19/§D-26). A remote_write token pasted in
    here is a token published to the world, and the failure is silent — it works perfectly.
    """
    alloy = ALLOY.read_text()
    for var in ("GRAFANA_CLOUD_PROM_URL", "GRAFANA_CLOUD_PROM_USER", "GRAFANA_CLOUD_PROM_KEY"):
        assert f'sys.env("{var}")' in alloy, f"{var} must be read from the environment"
    # A literal `glc_...` / `eyJ...` in the config would be a leaked Grafana Cloud token.
    assert not re.search(r"(glc_[A-Za-z0-9+/=]{10,}|eyJ[A-Za-z0-9_-]{20,})", alloy), (
        "what looks like a Grafana Cloud token is committed in the Alloy config"
    )
    example = (ROOT / "deploy" / "alloy" / "scs.env.example").read_text()
    for line in example.splitlines():
        if line.startswith(("GRAFANA_CLOUD_PROM_USER=", "GRAFANA_CLOUD_PROM_KEY=")):
            assert line.split("=", 1)[1].strip() == "", f"{line} must be an empty placeholder"


# ------------------------------------------------------------------ the label vocabularies are real


def _sources() -> str:
    return "\n".join(p.read_text() for p in sorted(SRC.rglob("*.py")))


@pytest.mark.parametrize(
    ("vocabulary", "pattern", "what"),
    [
        (TICK_PHASES, r'TICK_PHASE_SECONDS,\s*phase="([a-z_]+)"', "tick phases"),
        (CAPTURE_STAGES, r'CAPTURE_FAILURES\.labels\(stage="([a-z_]+)"\)', "capture stages"),
        (DASHBOARD_ARTIFACTS, r'record_dashboard_write\(\s*"([a-z_]+)"', "dashboard artifacts"),
        (IBKR_REQUEST_KINDS, r'ibkr_request\(\s*"([a-z_]+)"\)', "IBKR request kinds"),
    ],
)
def test_the_label_vocabularies_match_their_call_sites(
    vocabulary: tuple[str, ...], pattern: str, what: str
) -> None:
    """The closed sets in `monitoring.py` are the cardinality budget, so they must be true.

    They are documentation the moment nothing checks them, and stale documentation about
    cardinality is how a free tier gets spent. Both directions matter: a label used but not listed
    means the budget is understated, and a label listed but never used means a panel is selecting
    on a series that will never appear.
    """
    used = set(re.findall(pattern, _sources()))
    assert used, f"the {what} scanner matched nothing — has the call-site shape changed?"
    assert used == set(vocabulary), (
        f"{what}: used at call sites but not listed: {sorted(used - set(vocabulary))}; "
        f"listed but never used: {sorted(set(vocabulary) - used)}"
    )


# ------------------------------------------------------------------ the textfile producer


def test_the_freshness_probe_writes_atomically() -> None:
    """A half-written `.prom` file breaks the WHOLE textfile collector, not just this metric.

    node_exporter re-reads the directory on every scrape and a parse error there costs every
    textfile metric at once. Same reasoning as `storage.py`'s atomic part-file writes, and the same
    fix: build it elsewhere, rename it into place.
    """
    script = FRESHNESS.read_text()
    assert "mktemp" in script and 'mv "$tmp" "$OUT"' in script, (
        "the probe must build its output in a temp file and rename it into place"
    )
    assert '>>"$tmp"' in script or '>>"$tmp"' in script, "output is built in the temp file"
    assert '>>"$OUT"' not in script, (
        "appending straight to the served file can be read half-written"
    )


def test_the_freshness_probe_reports_failure_rather_than_going_silent() -> None:
    """Every exit path must write a verdict.

    If a failed probe wrote nothing, the last successful file would sit in the textfile directory
    unchanged and node_exporter would keep serving a stale age — a monitor whose failure mode is
    reporting that everything is fine.
    """
    script = FRESHNESS.read_text()
    assert script.count("scs_published_probe_success 0") >= 3, (
        "each failure path (no response, no stamp, unparseable stamp) must emit success=0"
    )
    assert "set -euo pipefail" in script


def test_the_freshness_timer_never_replays_at_boot() -> None:
    """The metric is 'how stale is it RIGHT NOW'.

    A `Persistent=true` timer replays a missed fire at boot and would write an age computed against
    a payload that has since been republished — a wrong number, presented as a current one.
    """
    timer = (ROOT / "deploy" / "scs-freshness.timer").read_text()
    assert "Persistent=false" in timer
    assert "OnUnitActiveSec=" in timer
    service = (ROOT / "deploy" / "scs-freshness.service").read_text()
    assert "Type=oneshot" in service
    assert not re.search(r"^Restart=", service, re.M), (
        "a restart loop against raw.githubusercontent.com is how the box's IP gets rate-limited"
    )
