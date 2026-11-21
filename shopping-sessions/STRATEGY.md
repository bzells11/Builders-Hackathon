# Build the oracle first, then race

**Bottom line:** build two things at once. One teammate writes a slow, plain-Python **oracle** (a reference version you trust to be right, used only to check answers) straight from `rules.md`. The other writes a **simple single-threaded Rust binary** shaped from day one like the fast version will be: its own byte-level parser, integer milliseconds, IDs turned into integers, and separate stages. Submit the Rust binary once it matches the oracle on the 36 fixtures, the dev answer and about 50 generator seeds. Then speed it up only behind the same diff harness. Off-the-shelf engines cannot be your parser. In sandbox tests, the DuckDB and Polars CSV readers broke this file format in several silent ways, and every library timestamp parser tested accepted inputs the rules call bad ([rules.md](https://www.joinbuilders.org/buildathon/tracks/shopping-sessions/files/rules.md)). That matters because one wrong row scores nothing and the prize is winner-take-all. Speed decides the winner among correct entries. The final is a fresh ~60M-event dataset, timed 3 times with a 3-minute cap each, and the top 5 or more are re-timed 5 times. So the 2.74 s leader on 20M is the bar, and the Rust architecture is the only path below it that we found. A tuned Rust pipeline is estimated at about 1–1.5 s on 20M and 2.5–4.3 s on 60M with a warm cache. Polars is estimated at about 3–4 s and 8–12 s. You have about 40 hours (cutoff Sun Nov 22, 11:00 AM ET), so the plan below gets a correct submission in by hour 12 and keeps the last few hours for checking only.

**How to read the evidence labels:** **[M]** means measured in the research sandbox (2 vCPU, DuckDB 2.0.1, Polars 2.1.0, Python 3.13; re-run it on the versions you pin). **[P]** means published in a cited source. **[E]** means an estimate or inference that has not been measured on the 8-vCPU scorer. Where the notes paraphrased the rules differently, `rules.md` (copied verbatim) wins. One example: one note recorded a leaderboard topping out at 1.52 s and said the Docker limits were unpublished. The verbatim rules list the limits and a Fri 6:50 PM board led by Wolverines at 2.74 s. Check the live board before you set your target.

## Rust wins the final, but only after a Python oracle says so

The workload is roughly **1.6 GB of event CSV at 20M and about 4.8–5.3 GB at 60M** [E, from a measured 80.6 bytes per row on a synthetic file]. Clicks and links are small next to that. The deciding facts are in the table. Spark loses on startup, since the baseline took 171.91 s ([rules.md](https://www.joinbuilders.org/buildathon/tracks/shopping-sessions/files/rules.md)). DuckDB and Polars lose on exactness at parse time, and on speed at 60M. Rust's only weakness is that bugs are harder for a beginner to see, and the oracle fixes that.

| Option | Parses this format exactly? | Ingest rate, 2 vCPU [M] | Startup | 20M est. [E] | 60M est. [E] | Role |
|---|---|---|---|---|---|---|
| Pure Python (bytes) | Yes, if you write it | slow (≈60–120 s for 20M to parse) | ~0 | minutes; may not fit in memory | too slow | **Oracle only** |
| DuckDB `read_csv` | **No**: `\r` is always treated as a line end, and stray quotes and 8-field lines are accepted | 0.22 GB/s | 0.087 s import [M] | 3–15 s | ≥10 s | Optional second opinion on logic |
| Polars `read_csv` | **No**: short rows are padded, `""` becomes `''`, `\r` is stripped | 0.53 GB/s; line mode 1.1 GB/s | 0.103 s import [M] | 3–4 s | 8–12 s | **Plan B** submission |
| Rust, own parser | Yes | ≈1 GB/s naive on 2 threads [M] | ~1 ms | 1–1.5 s tuned | 2.5–4.3 s warm | **The submission** |
| PySpark | No | — | several seconds (JVM) | 171.91 s baseline [P] | — | None |

DuckDB's docs confirm the `\r` problem can't be configured away: the parser "does not differentiate between '\r' and '\n'" ([DuckDB CSV docs](https://duckdb.org/docs/2.0/data/csv/overview.html)). DuckDB's published load rates of about **0.73–1.36 GB/s on an M1 Max** ([DuckDB taxi benchmark](https://duckdb.org/2024/10/16/driving-csv-performance-benchmarking-duckdb-with-the-nyc-taxi-dataset.html), [DuckDB CSV vs Parquet](https://duckdb.org/2024/12/05/csv-files-dethroning-parquet-or-not.html)) put the parse step alone at several seconds for 60M. Native code can go much faster. The fastest One Billion Row Challenge entry (1BRC, a public contest to aggregate a 1-billion-row text file as fast as possible) took **1.535 s on 8 cores**, and the 1BRC write-up says fast startup was decisive at that scale ([Morling, 1BRC results](https://morling.dev/blog/1brc-results-are-in)).

The migration path works because phase 1 already makes the decisions that phase 2 needs. Nothing gets thrown away. You add threads and SIMD (instructions that compare many bytes at once) inside functions that already exist.

| Phase-1 decision (make it now) | Why it keeps speed work open |
|---|---|
| Parser takes a byte range `&[u8]` and returns compact row structs | Later you call the same function on 8 memory-mapped (mmap) chunks in parallel |
| All times are `i64` milliseconds; day = `start.div_euclid(86_400_000)` | No date library in the hot loop, and the value is already Parquet DATE (days since epoch) |
| IDs are turned into `u32` numbers ("interned"); strings stay `&[u8]` | Fast hashing and sorting; byte-wise order comes free |
| Stages are separate modules (parse → identity → dedup/validate → sessions → attribution → output) | Each stage can be optimized and diffed on its own |
| `--debug-dump DIR` writes each stage's intermediate table, in both Rust and the oracle | A wrong output row gets traced to the stage that caused it |
| Chunk size and thread count are command-line flags | Tiny chunks on small data test the parallel boundaries against the oracle (see verification) |
| Pinned toolchain, committed `Cargo.lock`, multi-stage Dockerfile, build time measured | The final **rebuilds** your image, and the build has a 10-minute limit |

**Plan B, if Rust doesn't match the oracle by about hour 14:** submit Polars, but don't use its CSV reader as the parser of record. Read each file's bytes. If the file contains no `\r` and no `\x01` byte, read it in Polars "line mode" (`separator='\x01'`, `quote_char=None`, one string column per line), then split and validate with expressions. Otherwise, send that file through the exact Python parser. This is exact on generator data, since "the generator never writes a `\r`," and safe on fixtures [E].

## Twenty-three rules, each with a trap that fails silently

The required pipeline order is fixed by the rules: links first, then events (parse → dedup → validate → late filter → sessionize), then clicks (parse → dedup → validate), then attribution, aggregation and the Parquet write ([rules.md](https://www.joinbuilders.org/buildathon/tracks/shopping-sessions/files/rules.md)). Every row below needs its own unit case or fixture. The "Trap" column lists measured or documented ways the usual tools get each rule wrong without raising an error.

| # | Rule (rules.md) | Exact implementation | Silent trap |
|---|---|---|---|
| 1 | Only `\n` ends a line; `\r` is data | Split raw bytes on `b'\n'`; never strip | DuckDB, Polars and PyArrow all strip a `\r` before `\n`, so `1000\r` becomes a valid price [M] |
| 2 | Header row; some files are header-only; empty line is malformed | Drop line 1 of each file; `b''.split(b',')` has length 1, so the count check rejects it | Polars returns an empty line as an all-null row [M] |
| 3 | Wrong field count is malformed | Exact count: events 7, clicks 4, links 2 | Polars pads short rows; DuckDB non-strict keeps 8-field lines [M] |
| 4 | Quotes only around a whole field; `""` is null | A field with `"` must be `"…"`, length ≥ 2, no inner quote; otherwise drop the line. Strip quotes; empty becomes null | All three tools keep `u"1`; DuckDB reads `"u1"x` as `u1`; Polars makes `""` into `''` [M] |
| 5 | Spaces matter; ASCII digits only | No trim, no lowercase; check digits as bytes 0x30–0x39 | Python `int('٢')`=2, `int(' 5 ')`=5; Rust/Polars `\d` matches Unicode digits [M][P] |
| 6 | F1 / F2 / F3 strict | One hand-written parser: an all-ASCII-digit field is F3; otherwise exact `YYYY-MM-DDTHH:MM:SS` + (`Z` \| `±HH:MM`) | `fromisoformat` accepts space, fractions, no zone, `+0200`; DuckDB casts accept `+24:00` and `24:00:00` [M] |
| 7 | Impossible calendar values are bad | Check month 1–12, days-in-month with leap rule, h ≤ 23, m ≤ 59, s ≤ 59, offset h ≤ 23 / m ≤ 59, all **on the fields as written**, then subtract the offset | chrono accepts second 60, `t`, space and fractions [P]; `+05:60` silently becomes `+06:00` in Python [M] |
| 8 | F3 too long for 64-bit is out of window, not an error | Strip leading zeros; more than 18 digits counts as out of window; never wrap or panic | DuckDB `TRY_CAST` reads `1.79e12` and `1791071890000.0` as in-window; `epoch_ms(huge)` aborts the whole query [M] |
| 9 | Event window `ws ≤ ts < we`; click window `ws − 7d ≤ ts < we` | Integer compares; read `manifest.json` every run | `BETWEEN` is inclusive at `we`; `INTERVAL '7 days'` follows daylight saving in a non-UTC session [E] |
| 10 | Links: drop malformed/empty; transitive; self-links and repeats are harmless | Iterative union-find (path halving, union by size) over every link ID | Fixed-iteration label propagation in SQL breaks on chains hundreds of links long [E]; Python recursion limit [E] |
| 11 | Person name = byte-wise smallest ID, including link-only IDs | After all unions, take the min of bytes per root | Naming by the union-find root, or only by IDs seen in events; upper-case IDs sort first and can rename or split a person |
| 12 | Drop empty `event_id` at parse | Before dedup | — |
| 13 | Dedup on `event_id`, keep min `ingest_ts`, **before** validation | Hash map by full `event_id` bytes; the kept copy's fields are final, including `user_id` (1 in 64 later copies name another user) | Filtering first lets a later valid copy "rescue" the event; Polars `unique(keep='first')` depends on read order [E] |
| 14 | Validate: user, ts, type exactly `view\|cart\|purchase`, product non-empty; purchase price digits, 1..10,000,000 | Byte-equal type check; price: strip zeros, ≤ 8 digits, value 1..=10M; ignore price for view/cart | DuckDB casts accept `+5` and `1.79e12`, Polars accepts `+5`/`-5` [M]; `0000` is 0, so invalid |
| 15 | Late: drop if `ingest − ts > 86,400,000` | Strict `>` on i64; negative gap kept | `>=`, `abs()`, float seconds |
| 16 | Sessionize per **person**, sorted by ts; new session if gap > 1,800,000 | Boundary when the person changes or the gap is over 1,800,000; equal ts give gap 0 automatically | `>=`; measuring from session start; sessionizing per raw `user_id` |
| 17 | Day = UTC date of session start; events after midnight count on that day | `start.div_euclid(86_400_000)` | DuckDB `TIMESTAMPTZ::date` and Polars after `convert_time_zone` give the **local** date [M] |
| 18 | Unknown product → `unknown` | Exact lookup in products.csv parsed with the same parser | Trimming/case-folding product IDs |
| 19 | Clicks: drop empty `click_id`, dedup, validate; unknown campaign is **dropped** | Exact `campaign_id` lookup | Falling back to organic for unknown campaigns |
| 20 | Credit: latest click with `ts ≤ start` and `start − ts ≤ 604,800,000`; tie → smallest `click_id` | Sort the person's clicks by (ts, click_id); keep the **first** click_id per ts; one pointer; then check the 7-day limit | Polars `join_asof` returns the **largest** tied ID; DuckDB ASOF returns whichever row was inserted first [M] |
| 21 | Assists: each distinct campaign with a click in `[start − 7d, start]`, credited one included; organic never | Two-pointer sliding window over all valid clicks, with per-campaign counts | Range joins blow up for whale users [E]; forgetting to include the credited campaign |
| 22 | user_daily `top_category`: max purchase sum, tie → byte-smallest, `unknown` competes, null if no purchases | Sort by (sum desc, category asc), take the first | `arg_max`/`mode` break ties arbitrarily |
| 23 | Output: exact columns, `day` is Parquet DATE, files directly in each folder, assist-only campaign rows with zeros | arrow-rs `Date32`, Int64 counts, no partition subfolders | DuckDB `sum(BIGINT)` is written as **DOUBLE**; `date_trunc` and Polars `dt.truncate` write TIMESTAMP [M] |

The run environment has its own traps. Read `DATA_DIR`, `OUTPUT_DIR` and `SCRATCH_DIR` from the environment. Put any spill files in `/scratch`, because `/tmp` is a **64 MB in-memory tmpfs**. Log only to stderr, since stdout is capped at 50 MB. Expect to run as uid 1000 on a read-only root, with no network at run time ([rules.md](https://www.joinbuilders.org/buildathon/tracks/shopping-sessions/files/rules.md)). Never call `String::from_utf8(..).unwrap()` on input. Invalid UTF-8 would panic, the exit code would be nonzero, and the run would score zero [E]. Rust's `regex` crate makes `\d` Unicode-aware by default ([regex docs](https://docs.rs/regex/latest/regex/)). chrono's RFC 3339 parser accepts `t`, a space, any number of fractional digits, and second 60 ([chrono source](https://docs.rs/chrono/latest/src/chrono/format/parse.rs.html)). So neither belongs anywhere in the parser. Byte-wise ordering itself is safe in Python, Polars and DuckDB's default collation, all of which matched UTF-8 byte order on a mixed test set [M]. JavaScript and Java order some characters differently because they compare UTF-16 [M].

## Nothing ships until five layers of tests agree

Differential testing means feeding the same input to two independent implementations and treating any disagreement as a bug in one of them ([Wikipedia](https://en.wikipedia.org/wiki/Differential_testing)). Here, the oracle is trusted only after it passes the official evidence. After that, it judges everything else. The 1BRC organizers found entries that were fast but gave wrong results, and entries whose speed collapsed on differently shaped input ([foojay, 1BRC lessons](https://foojay.io/today/12-lessons-learned-from-doing-the-one-billion-row-challenge)). Both failure modes apply here.

| Layer | What to run | Pass criterion | Catches |
|---|---|---|---|
| 0. Shared case tables | `tests/cases/{lines,timestamps,prices}.tsv`, one row per example in rules.md; both implementations unit-test against it. Add Hypothesis (a Python library that generates random test inputs) to fuzz Rust's parser against Python's on the same bytes | 100% agree | Parser and timestamp divergence (rules 1–8, 14) |
| 1. 36 starter fixtures | The starter's scripts, which run the image like the scorer and show differing rows | 0 differing rows, oracle **and** Rust | Hand-made edge cases, including `\r` |
| 2. Dev answer (seed 1, 1M) | `make check-dev` against `dev-answer.zip` | 0 diffs | Realistic dirt mix |
| 3. Seed sweep vs oracle | 100+ seeds at 1k–50k events, 10–20 seeds at 1M; include seeds whose window contains **2028-02-29**, plus month/year edges | 0 diffs; stop at the first failing seed and shrink it into a new fixture | Rare combinations |
| 4. Parallel-boundary stress | Rust with `--chunk-bytes 4096 --threads 8` vs `--threads 1`, vs the oracle on small seeds | Identical | Chunk-split, merge and race bugs, without needing a 60M oracle |
| 5. Invariants at 20M/60M | rules.md self-checks plus stronger ones (below) | All hold | Overflow, hash collisions, skew bugs at scale |

The stronger invariants all follow from the rules. They need no oracle, so they also run at 60M. **Per day**, summed `sessions` and `revenue_cents` match across the two tables. Every non-organic `campaign_daily` row has `assisted_sessions ≥ sessions`, because the credited click is always inside the assist window. `0 ≤ purchasing_sessions ≤ sessions`, and `revenue_cents = 0` exactly when `purchasing_sessions = 0`. In `user_daily`, `events ≥ sessions ≥ 1`, and `top_category` is null exactly when revenue is 0. Every `day` falls inside the window's 14 dates. Add metamorphic tests too: change the input in a way that should not change the answer, then demand identical output. Examples: shuffle or split files, re-quote fields, swap link columns, add self-links, rewrite valid timestamps between F1, F2 and F3, add header-only files, or remove the click-window filter. That last one is safe because any click within 7 days of a session already lies inside the click window.

The diff tool should behave like the scorer. First check the schema with pyarrow: exact column set, `day` is DATE, integers are integers. Then check key uniqueness. Then do a full outer join on `(user_id, day)` and `(campaign_id, day)` with null-safe comparison, report missing, extra and differing counts, and print the first 20 rows of each. Cast integers to BIGINT first so a width mismatch doesn't show up as a diff. If you diff in SQL, use `EXCEPT ALL`, because plain `EXCEPT` removes duplicates and hides duplicate-row bugs ([DuckDB set operations](https://duckdb.org/docs/2.0/sql/query_syntax/setops.html)). Bend the generator's Rust source to add "tie storm" modes: many clicks at the same millisecond, gaps of exactly 30:00.000, clicks exactly 7 days back, ingest exactly 24 h after ts, and one huge chain. Targeted boundary data beats more random seeds.

Run locally with the scorer's limits before spending a submission. You get 2 per hour, and the hidden sets return only an exit code, an OOM flag and row-diff counts. Prefer the starter's own run script, and compare its flags with these, which come from the limits in rules.md and Docker's documented options ([Docker resource constraints](https://docs.docker.com/engine/containers/resource_constraints/), [Docker tmpfs](https://docs.docker.com/engine/storage/tmpfs/)):

```bash
docker build --platform linux/amd64 -t ss .        # time this: must stay well under 10 min
mkdir -p out scratch && chmod 777 out scratch
/usr/bin/time -v docker run --rm --platform linux/amd64 \
  --cpus=8 --memory=24g --memory-swap=24g --pids-limit=4096 \
  --read-only --tmpfs /tmp:rw,size=64m,mode=1777 --user 1000:1000 --network none \
  -e DATA_DIR=/data -e OUTPUT_DIR=/output -e SCRATCH_DIR=/scratch -e HOME=/scratch -e NCPU=8 -e MEM_GB=24 \
  -v "$PWD/data:/data:ro" -v "$PWD/out:/output" -v "$PWD/scratch:/scratch" ss
```

Setting `--memory-swap` equal to `--memory` disables swap, which matches the scorer. For realistic timings, use an hourly-billed GCP n2-standard-8, the scorer's machine family; a laptop's numbers won't transfer. Keep a submission log with commit hash, local diff results and scorer counts. If the scorer reports differing rows that the local tests never showed, a rule went untested, so go back to the boundary-forcing generator modes.

## Parsing is half of a 2.5–4.3 second 60M budget

The bottom-up budget below is an estimate [E] for a tuned Rust binary on 8 vCPUs with a warm page cache (input files already in memory). It implies **about 1–1.5 s on 20M**, which would beat the current 2.74 s. Treat every number as a hypothesis until you print per-phase timestamps from your own 20M and 60M generated runs.

| Phase (60M events) | Est. wall time [E] |
|---|---|
| Start, mmap files, spawn threads | 0.01–0.03 s |
| Parse events, clicks and links (≈5.6 GB at 2.5–4 GB/s total) | **1.4–2.2 s** |
| Union-find over ≈7M links, plus min names | 0.05–0.15 s |
| Dedup (hash-partitioned) plus validation | 0.3–0.5 s |
| Map users to persons, scatter into partitions | 0.2–0.4 s |
| Per-partition sort, sessionize, attribute, aggregate | 0.3–0.6 s |
| Write Parquet (≈6–10M + 30k rows, uncompressed, parallel files) | 0.15–0.4 s |
| **Total, warm cache / cold network disk** | **≈2.5–4.3 s / ≈7–8 s** |

The cold-disk line comes from GCP's **800 MiB/s read cap for an 8-vCPU N2 on persistent disk** ([GCP disk performance](https://docs.cloud.google.com/compute/docs/disks/performance)). The leaders' 2.74 s on about 1.6 GB suggests the scorer's cache is warm, but that is unconfirmed. To be safe, overlap reading with parsing. The ranked roadmap below orders work by expected gain for your effort. Each step should land only with layers 3–5 still green.

| Rank | Optimization | Expected gain | Evidence |
|---|---|---|---|
| 1 | mmap + ~16 MB chunks across **all** files (sizes follow a Zipf distribution, so a few files are huge), boundaries moved to the next `\n`, chunks handed out by an atomic counter so idle threads take more work | ≈5–7× | 1BRC Rust 21 s → 3.5 s ([nibtr](https://nibtr.github.io/posts/1brc-rs)); Java 66 s → 17 s ([QuestDB](https://questdb.com/blog/billion-row-challenge-step-by-step/)) |
| 2 | Byte parser: no `String`, no UTF-8 checks, fixed-position timestamp digits, direct integer parsing | ≈2–3× on parse | 159 s → 59 s from byte keys alone ([nibtr](https://nibtr.github.io/posts/1brc-rs)) |
| 3 | Open-addressed hash tables with a fast hash; dedup and interning hash-partitioned so they need no locks; **compare full bytes on a hash match** | ≈1.5–2× on those phases | 11 s → 6.6 s ([QuestDB](https://questdb.com/blog/billion-row-challenge-step-by-step/)) |
| 4 | SIMD scanning for `,`, `"` and `\n` with `memchr` or AVX2 / AVX-512BW at 256-bit width | ≈1.3–2× on parse | memchr 33.5 s → 25.4 s ([nibtr](https://nibtr.github.io/posts/1brc-rs)); `memchr` picks AVX2 at runtime ([docs](https://docs.rs/crate/memchr/latest)) |
| 5 | No global sort: route rows by person into 256+ partitions, sort locally; rayon (a Rust work-stealing thread pool) evens out whale users | ≈0.5–1 s [E] | Design estimate |
| 6 | Pre-sized buffers, mimalloc, skip `munmap`/drop at exit (`process::exit`) | ≈0.1–0.3 s | ≈100 ms saved on unmapping ([QuestDB](https://questdb.com/blog/billion-row-challenge-step-by-step/)) |
| 7 | Parallel, uncompressed Parquet files | ≈0.1–0.3 s | Parquet writing is CPU-bound ([tpchgen-rs](https://datafusion.apache.org/blog/2025/04/10/fastest-tpch-generator/)) |
| 8 | I/O prefetch (`fadvise WILLNEED`) | Large only if the cache is cold | [GCP](https://docs.cloud.google.com/compute/docs/disks/performance) |
| 9 | Fat LTO, `codegen-units=1`, target-cpu tests, then PGO (profile-guided optimization) | ≈5–15% [E] | AVX-512 can lower clocks: a Skylake-SP chip dropped to ≈1.4 GHz under all-core use ([Cloudflare](https://blog.cloudflare.com/on-the-dangers-of-intels-frequency-scaling/)) |

Skip simd-csv's quote-aware readers. Its own docs say a mid-field quote "can corrupt the output", and it trims CRs and skips empty lines ([simd-csv docs](https://docs.rs/crate/simd-csv/latest)). All three break the rules. Hashing `event_id` to only 64 bits has roughly a **1-in-10,000 chance of a collision** among about 62M keys [E]. In a winner-take-all contest, compare the original bytes whenever two hashes match. Times vary about 20% across scorer machines ([rules.md](https://www.joinbuilders.org/buildathon/tracks/shopping-sessions/files/rules.md)), so a gain under about 3% is noise. Chase structural wins and stable run times. Re-time Docker build time after adding fat LTO and the `arrow`/`parquet` crates, because a build over 10 minutes fails the submission outright.

## The first twelve hours, split between Engine and Truth

Person A ("Engine") owns the Rust binary. Person B ("Truth") owns the oracle, the case tables and every test tool. Each prompts Claude Code separately from `rules.md`, so a misreading by one Claude session is unlikely to land in both implementations.

| Hours | Person A: Engine (Rust) | Person B: Truth (oracle + harness) |
|---|---|---|
| 0–1 | **Together:** read rules.md aloud; unzip the starter, fixtures, `dev-answer.zip` and `generator.zip`; build the generator; make the repo with `.gitignore` (data/, out/, target/, scratch/; repo must stay ≤ 50 MB); agree on the `--debug-dump` file formats | same |
| 1–4 | Dockerfile (multi-stage, pinned toolchain); env vars; manifest; file listing; line parser and timestamp parser passing the shared case tables | Write `tests/cases/*.tsv` first (hour 1–2) and push; oracle parser, timestamps and prices; Hypothesis tests |
| 4–8 | Union-find and names; dedup → validate → late filter; sessions; credit/assists; aggregates; arrow-rs Parquet with `Date32` | Oracle links → outputs; get **all 36 fixtures plus dev answer at 0 diffs** (this unlocks everything else) |
| 8–10 | Fix fixture failures, using stage dumps against the oracle's | `diff.py` (schema + scorer-style diff), `run_local.sh` (flags above), `sweep.py`, `invariants.py` |
| 10–12 | Rust passes fixtures, dev and a 50-seed sweep → **first submission**; record it in the log | Run the sweep; turn every failure into a fixture; find Feb-29 seeds |

After hour 12, take turns sleeping. On Saturday, A works through roadmap items 1–5 in order, one item per commit and one submission per hypothesis. Meanwhile B adds the tie-storm generator modes, metamorphic tests and the 20M/60M invariant runs on an n2-standard-8. If Rust is still failing diffs by about hour 14, B ships Plan B (Polars) as insurance. **Freeze features by about Sun 8:00 AM ET.** The last three hours are for a clean rebuild from a fresh clone, the full test suite, and confirming that the commit live at 11:00 AM is the verified one.

### Kickoff prompt for Person A (paste into Claude Code)

```text
You are building the scored submission for the Databricks "Shopping Sessions" hackathon. Correctness is pass/fail
(one wrong row scores nothing); speed ranks correct entries. rules.md in this repo is the ONLY spec: read it fully
first, and quote the rule you implement in a comment above each function. Today's goal is a CORRECT, simple,
single-threaded Rust binary whose structure is ready for later parallelism. Do not optimize yet.

Environment (rules.md): linux/amd64, 8 vCPU, 24 GB, no swap, no network at run time, uid 1000, read-only root,
/tmp is only 64 MB. Read env DATA_DIR, OUTPUT_DIR, SCRATCH_DIR. Write Parquet into $OUTPUT_DIR/user_daily/ and
$OUTPUT_DIR/campaign_daily/ (files directly in each folder). Log to stderr only. Docker build must take <10 min.

Hard requirements:
1. Own CSV parser on raw bytes. Split on b'\n' only (\r is ordinary data, never strip it). Skip each file's header
   line. Split lines on b','. Require an exact field count (events 7, clicks 4, links 2, products 2, campaigns 2);
   an empty line fails this. A field containing '"' is valid only if it is exactly "<no quotes inside>"; strip
   them. Any other quote drops the whole line. An empty value (including "") is null. Never trim, lowercase or
   UTF-8-validate. Keep IDs as bytes. Never panic on input.
2. Own timestamp parser. No chrono, no regex, no str::parse on unchecked text. An all-ASCII-digit field is F3 epoch
   ms: strip leading zeros; more than 18 digits means out of window. Otherwise accept exactly
   YYYY-MM-DDTHH:MM:SS followed by Z or +HH:MM/-HH:MM. Check month 1-12, days in month with the Gregorian leap rule,
   hour<=23, min<=59, sec<=59, offset hour<=23 and min<=59 on the written fields. Then compute i64 ms with
   days_from_civil and subtract the offset. Use i64 milliseconds everywhere.
3. Pipeline order: links -> iterative union-find (path halving, union by size) -> person name = byte-wise
   smallest ID in the component, including link-only IDs. Events: drop malformed or empty event_id -> dedup by
   event_id keeping min ingest_ts (BEFORE validation; compare full bytes) -> validate the kept copy (user
   non-empty; ts valid with ws<=ts<we; type exactly view|cart|purchase; product non-empty; purchase price ASCII
   digits with value 1..=10_000_000) -> drop if ingest-ts > 86_400_000 -> map user to person -> sort by
   (person, ts) -> new session when gap > 1_800_000 -> day = start.div_euclid(86_400_000).
   Clicks: drop malformed or empty click_id -> dedup by click_id -> validate (user non-empty; ts valid with
   ws-604_800_000<=ts<we; campaign_id exactly in campaigns.csv; unknown campaign is DROPPED, never organic).
   Credit = latest click with ts<=start and start-ts<=604_800_000, tie -> smallest click_id bytes; none ->
   campaign 'organic', channel 'organic'. Assists = each distinct campaign with any valid click in
   [start-604_800_000, start], credited one included; organic never assists.
4. Outputs exactly as rules.md: user_daily(user_id, day, sessions, events, revenue_cents, top_category) and
   campaign_daily(campaign_id, channel, day, sessions, purchasing_sessions, revenue_cents, assisted_sessions),
   including assist-only rows with zeros. top_category = max summed purchase price, tie -> byte-smallest,
   'unknown' competes, null if no purchases. day uses arrow Date32 (Parquet DATE); counts and sums are Int64.
5. Structure for later speed: modules parse, ts, identity, events, clicks, sessions, attribution, output.
   Parse functions take a &[u8] chunk and return compact structs. Intern IDs to u32. Add flags --threads,
   --chunk-bytes, and --debug-dump DIR (CSV of: person map; surviving events with person and session start;
   per-session credit and assists) matching the oracle's dump format in tests/DUMP_FORMAT.md.
6. cargo tests driven by tests/cases/*.tsv. rust-toolchain.toml pinned, Cargo.lock committed, multi-stage
   Dockerfile, release profile lto="thin" for now. Report the docker build time.
Done when: all 36 starter fixtures and `make check-dev` show 0 differing rows. For any failure, show the rows,
quote the rule, then fix.
```

### Kickoff prompt for Person B (paste into Claude Code)

```text
You are building the correctness oracle and test harness for the "Shopping Sessions" hackathon. It will judge
a fast Rust implementation. rules.md is the ONLY spec: read all of it first. Write plain, obviously-correct
Python 3. Speed does not matter; clarity does. One function per rule step, with the rule text quoted in its
docstring. Use the stdlib for logic, pyarrow only for Parquet I/O, and hypothesis for tests.
Forbidden in the oracle: csv module, pandas/Polars/DuckDB readers, datetime.fromisoformat/strptime, int() on
unchecked text, str.isdigit(), regex without re.ASCII and fullmatch, local-time functions, and recursion in
union-find. Keep IDs as bytes. Use integer ms everywhere; day = date(1970,1,1) + timedelta(days=start_ms // 86_400_000).
For credit and assists, brute-force scan each session against all of the person's valid clicks.
Write tests/DUMP_FORMAT.md and implement --debug-dump DIR (person map; surviving events with person and session
start; per-session credit and assists).

Build, in this order:
1. tests/cases/{lines,timestamps,prices}.tsv (escaped bytes -> expected result) covering every rules.md example:
   1000\r, ...Z\r, "u1", "", u"1, "u1"x, 6 and 8 fields, empty line, ' u1' vs 'u1 ', Arabic and full-width
   digits, F1/F2/F3, -00:00, +24:00, +05:60, +0200, fractional seconds, lowercase z/t, space for T, no zone,
   month 13, Feb 30, Feb 29 2026 vs 2028, hour 24, second 60, 1791071890000.0, 1.79e12, -5, leading-zero F3,
   86400000, a 25-digit F3, prices 0250, 0, 0000, 10000000, 10000001, +5, 1.5, empty. Push this first.
2. oracle.py DATA OUT: must reach 0 diffs on all 36 starter fixtures and on dev-answer.zip (seed 1).
3. tools/diff.py A B: pyarrow schema check (exact columns, day is DATE, ints are ints, *.parquet directly in the
   folder, ignore files starting with . or _), key uniqueness, full outer join on (user_id, day) and
   (campaign_id, day) with null-safe compare; print missing/extra/differing counts and the first 20 of each.
4. tools/run_local.sh IMAGE DATA OUT: docker run with the scorer limits listed in README, wall-clock timed.
5. tools/sweep.py: generate seeds at 1k/10k/50k/1M events with the official generator; run oracle and image;
   diff; stop at the first failure and print the seed. Also list seeds whose window contains 2028-02-29.
6. tools/invariants.py OUT: rules.md self-checks, plus per-day equality of sessions and revenue across tables,
   assisted_sessions >= sessions for non-organic rows, 0 <= purchasing_sessions <= sessions,
   revenue_cents == 0 iff purchasing_sessions == 0, top_category null iff revenue 0, events >= sessions,
   and every day inside the window.
```

## Conclusion

The hard part of this track is agreement, not code. Claude Code can write either implementation in an afternoon. What wins is a setup where a silent divergence can't survive to the final: library-free parsing, integer time, explicit tie-breaks, and a diff harness that compares at every stage, every seed and every chunk size. That setup also makes speed work low-risk, because every optimization is a refactor with an automatic referee. The biggest unknowns are whether the scorer's disk cache is warm (it decides whether 60M lands near 3 s or near 7 s for everyone) and how fast the leaders are on 60M rather than 20M. Your first scorer runs, with per-phase timing printed to stderr, should answer both before you put Saturday's effort into micro-optimizations.
