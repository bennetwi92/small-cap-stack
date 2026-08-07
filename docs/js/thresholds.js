// When published data counts as stale (#516).
//
// These were duplicated with *different values*: the status bar warned at 30 minutes while the
// Plan page's "Data freshness" check called the same `status.json.generated_utc` fresh until 60.
// Both render on the Plan page, so between 30 and 60 minutes old it showed a FRESH check row
// above an amber status bar reporting the same timestamp. One of them had to be wrong, and a
// reader had no way to tell which.

// `publish-dashboard` runs on a `*/15` cron, so age is best read in missed publish cycles.
//
// Three, not two. The workflow's own comment records that "GitHub's scheduled runs can lag under
// load", so a two-cycle threshold flags normal scheduler jitter — and a status bar that goes amber
// when nothing is wrong is one people learn to ignore, which is worse than not having it. Three
// consecutive misses is outside jitter and worth looking at.
//
// This is a judgement call, not a measurement: it moved the bar's warning later (30 -> 45) and the
// Plan check's earlier (60 -> 45). If publish cadence changes, change this, not the call sites.
export const STALE_PUBLISH_MS = 45 * 60 * 1000;

// Hours without the harvest checkpoint moving before the panel calls it stalled (#454).
//
// The job runs nightly, so anything past ~36h means a night produced nothing. That is the ONLY
// failure signal there is: the harvest sits deliberately outside the tracker's dead-man's switch,
// and its unit finishing with nothing to do looks identical to its having died (#450).
export const HARVEST_STALE_H = 36;
