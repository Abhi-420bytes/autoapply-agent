<p align="center">
  <img src="branding/logo.png" alt="AutoApply by Abhiram" width="560">
</p>

<p align="center">
  <a href="https://github.com/Abhi-420bytes/autoapply-agent/releases/latest/download/AutoApply-macos-arm64.dmg"><img alt="Download for Mac (Apple Silicon)" src="https://img.shields.io/badge/Download-Mac%20(Apple%20Silicon)-4f46e5?style=for-the-badge&logo=apple&logoColor=white"></a>
  <a href="https://github.com/Abhi-420bytes/autoapply-agent/releases/latest/download/AutoApply-macos-intel.dmg"><img alt="Download for Mac (Intel)" src="https://img.shields.io/badge/Download-Mac%20(Intel)-6366f1?style=for-the-badge&logo=apple&logoColor=white"></a>
  <a href="https://github.com/Abhi-420bytes/autoapply-agent/releases/latest/download/AutoApply-windows-setup.exe"><img alt="Download for Windows" src="https://img.shields.io/badge/Download-Windows-7c3aed?style=for-the-badge&logo=windows&logoColor=white"></a>
</p>

<p align="center"><b>Created by <a href="https://github.com/Abhi-420bytes">Abhiram</a> (Challa Abhiram)</b> · <a href="LICENSE">MIT License</a></p>

# AutoApply Agent

An open-source **agentic AI** job-search assistant. It reads job emails and job websites,
tailors your LaTeX resume to each job description (using only facts from your GitHub, past
resumes and profile), applies on company portals with your approval, and sends tailored cold
emails to startups and mid-size companies.

- **Tailored resumes:** a LangGraph write → ATS-score → review loop, with an ATS reviewer
  agent that tells the writer how to improve each draft. A truthfulness guard removes
  anything that isn't in your data. Your LaTeX template stays locked, and each resume fits
  your page limit.
- **Job sources:** Gmail/Outlook (job alerts, LinkedIn alerts split per posting), job
  websites and placement portals, and startup directories (e.g. bangalorestartupmap.com).
- **Applying:** a Playwright agent learns each portal's form (Thompson-sampling rewards). It
  never bypasses CAPTCHA or OTP and never applies on LinkedIn itself.
- **Outreach:** finds a company's published hiring email (or watches its careers page),
  writes a short email about your most relevant projects, and attaches your tailored resume.
  Every email says it was sent by AutoApply Agent.
- **Private by design:** everything runs on your computer. API keys and tokens are encrypted
  (Fernet), and any LLM provider works through one gateway (Gemini, OpenAI, Anthropic,
  Ollama…).

## Download (no Docker needed)

Get the app from the **[Releases page](https://github.com/Abhi-420bytes/autoapply-agent/releases/latest)**:

| System | Direct download | First launch |
|---|---|---|
| **Mac, Apple Silicon** (M1/M2/M3/M4) | [AutoApply-macos-arm64.dmg](https://github.com/Abhi-420bytes/autoapply-agent/releases/latest/download/AutoApply-macos-arm64.dmg) | Drag AutoApply to Applications, then **right-click → Open → Open** the first time (the app isn't Apple-notarized). |
| **Mac, Intel** | [AutoApply-macos-intel.dmg](https://github.com/Abhi-420bytes/autoapply-agent/releases/latest/download/AutoApply-macos-intel.dmg) | Same as above. |
| **Windows 10/11** | [AutoApply-windows-setup.exe](https://github.com/Abhi-420bytes/autoapply-agent/releases/latest/download/AutoApply-windows-setup.exe) | If SmartScreen appears: **More info → Run anyway**. Tick "Start AutoApply when I sign in" to keep the agent working. |

These links always download the latest version.

Open AutoApply and the dashboard opens in its own window. Everything runs on your own
computer, and your data stays in your user folder. On first launch the app:

1. creates its own encryption key,
2. downloads the browser used by the portal agent (about 150 MB, once),
3. downloads LaTeX packages the first time it builds a resume.

Then add an AI key in **Settings → AI models** (a free Gemini key works), upload your LaTeX
resume in **Resume template**, and connect your email in **Settings → Email accounts**.
For Gmail, the simplest option is IMAP with an app password.

Developers can still run the Docker setup described below, or run
`python -m app.desktop` from `backend/`.

> **Credit:** AutoApply Agent is free to use, modify and share under the MIT license, which
> requires every copy to keep the copyright notice and credit to its creator, Abhiram.

It's built in phases, and each phase is tested before the next one starts.

| Phase | Scope | Status |
|---|---|---|
| 1 | DB schema, FastAPI skeleton, Docker Compose, encrypted settings | ✅ done |
| 2 | LLM gateway (LiteLLM) + LLM Settings page | ✅ done |
| 3 | Template upload, marked-region filling, Tectonic, page-count check | ✅ done |
| 4 | GitHub + past-resume ingestion, RAG, re-index on embedding change | ✅ done |
| 5 | JD analyzer, resume generator, ATS loop, manual mode | ✅ done |
| 6 | Dashboard | ✅ done |
| 7 | Email accounts, watchers, link safety, apply modes, scheduling | ✅ done (needs your inbox credentials) |
| 8 | Playwright portal agent | ✅ done (needs Havlock selectors + login) |
| 9 | Outcome learning + analytics | ✅ done |

## Layout

```
backend/            FastAPI app, worker, Alembic migrations, tests
  app/api/          HTTP routes (settings, health)
  app/core/         process config from environment
  app/db/           engine/session, custom column types (encryption, vectors)
  app/models/       SQLAlchemy models for every table in the data model
  app/schemas/      Pydantic request/response + settings schemas
  app/security/     Fernet encryption, masking, log redaction
  app/services/     settings + audit logic
  app/llm/          the LLM gateway — the only path to any model (see Phase 2)
  app/email/        MIME parsing, providers (Graph/Gmail/IMAP), OAuth, link safety, ingestion + scheduling
  app/portal/       Playwright portal agent + per-portal selector configs (portal_configs/*.yaml)
  app/learning.py   outcome learning (bullet scores from shortlists/offers)
  app/generation/   JD analysis, fact pack + truthfulness guard, ATS scoring, LangGraph pipeline, run queue
  app/knowledge/    GitHub + past-resume ingestion, bullet bank, embedding sync, retrieval
  app/latex/         regions + template lock, content sanitizer, Tectonic compiler, PDF checks, fitting
  prompts/          versioned prompt templates (<name>/v<N>.md), editable without code changes
  templates/        sample marked template + Tectonic cache warm-up document
  app/cli.py        gen-key / rotate-keys
  app/worker.py     APScheduler worker (persistent job store)
frontend/           Next.js 14 dashboard (LLM Settings page in Phase 2)
docker-compose.yml  db (Postgres 16 + pgvector), redis, api, worker, frontend
```

## Phase 1 design

**Configuration has two layers.** `.env` holds only the bootstrap values: the DB URL, the
master encryption key and the API token. Everything you change from the dashboard is stored
in the `settings` table and validated by Pydantic (`AppSettings`, `Profile`). Defaults follow
the hard rules: `auto_apply=false`, `page_limit=1`, `ats_threshold=80`,
`global_delay_minutes=60`, `max_quality_iterations=4`.

**Secrets are encrypted at the column level.** `EncryptedText` and `EncryptedJSON` are
SQLAlchemy column types that Fernet-encrypt on write and decrypt on read. Every credential
column uses them: `llm_providers.encrypted_api_key`, `email_accounts.encrypted_oauth_tokens`,
`portals.encrypted_credentials` and secret settings. Because encryption happens in the column
type, no code path can write plaintext by mistake. The tests read the raw rows to prove this.

- **Masking.** The API only ever returns masked values (`sk-...a1b2`). No endpoint returns a
  plaintext secret.
- **Log redaction.** A logging filter scrubs known key and token shapes. It also scrubs any
  secret value that has been decrypted at runtime. Audit-log details are redacted recursively.
- **Key rotation.** `MultiFernet` accepts old keys during a rotation (see below).

**API auth.** Every `/api/*` route needs `Authorization: Bearer $API_TOKEN`. Only `/health`
is public, for Docker healthchecks. The dashboard sends the token from the Next.js server, so
it never reaches the browser. All ports bind to `127.0.0.1`.

**Data model.** All tables from the spec exist now, so later phases add behaviour rather
than reshaping the schema:

- `jobs`, `portals`, `email_accounts`, `sender_rules`, `source_emails`
- `job_files`, which holds attachments and screenshots
- `llm_providers`, `llm_task_config`, `llm_usage`
- `resumes`, which are immutable versions grouped by `lineage_id`
- `bullets`, `resume_bullets`, which link bullets to resumes for outcome learning
- `github_repos`, `embeddings`, `applications`, `outcomes`, `settings`, `audit_logs`

Some design choices in the schema:

- **Embeddings** store `model_name` and `dimension` on every row, so vectors from different
  models are never mixed. The column is pgvector `vector` on Postgres and JSON in the SQLite
  unit tests.
- **Enums** are stored as `VARCHAR` with a `CHECK` constraint, not native PG enums, so adding
  a status later is a one-line migration.
- **Deduplication:** `jobs.dedupe_key` is unique, and `source_emails` keeps the RFC
  `internet_message_id`. Together they let the same notice arriving in both inboxes merge
  into one job in Phase 7.

**Worker.** APScheduler with a SQLAlchemy job store, so delayed jobs survive restarts. For
now it runs only a heartbeat. Email polling, delayed processing and the weekly GitHub sync
get registered here in later phases. Redis is in the stack but unused until Phase 7.

## Phase 2 design: the LLM gateway

```
any module ──> LLMGateway ──> LLMBackend (LiteLLM) ──> Anthropic / OpenAI / Gemini / Groq /
  (task name)   routing, retries,     the only file that         OpenRouter / Ollama / custom
                fallback, schemas,    imports a provider SDK     OpenAI-compatible / Voyage /
                budget, cost log                                 local sentence-transformers
```

**Provider-agnostic (rule 9).** Code asks for a *task*, never a model:
`gateway.structured(LLMTask.JD_ANALYZER, "jd_analyzer", vars, JDSchema)`. The dashboard
decides which provider and model serve each task. `tests/test_architecture.py` fails the
build if any module other than `app/llm/backend.py` imports `litellm`, `openai`, `anthropic`
or another provider SDK.

**Routing, retries and fallback.** Each task has a primary and an optional fallback.
- A transient error (rate limit, timeout, 5xx or connection failure) gets one retry after
  a backoff, honouring `Retry-After` when the provider sends it. Then the gateway switches
  to the fallback, which gets the same treatment.
- An auth, bad-request or unknown-model error skips the retry and goes straight to the
  fallback, since retrying the same call can't help.
- Every switch is logged. Every attempt is recorded in `llm_usage`: task, model, tokens,
  cost, latency, whether it was the fallback, and the error (redacted).

**Structured output that works on every provider.** The Pydantic schema is rendered into
the prompt (`prompts/structured_output`). The reply is parsed tolerantly: code fences and
surrounding prose are fine. It is then validated, and on failure the validation errors go
back to the model as a repair prompt, up to 2 repairs. This doesn't depend on native JSON
modes, so Ollama and Groq behave the same as Claude.

**Prompts** live in `backend/prompts/<name>/v<N>.md`.
- Templates are sandboxed Jinja2, and a missing variable is an error.
- Files are re-read on every call. Docker bind-mounts the folder, so edits apply
  immediately without a restart.
- The newest version is the default. A task can pin an older one through its
  `prompt_version` parameter.
- Each usage row records which prompt version was used, e.g. `jd_analyzer.v2`.

**Embeddings.**
- Every vector is stored with a model key such as `openai:text-embedding-3-small` and its
  dimension.
- Changing the embedding model returns a warning (HTTP 409) until you confirm it. Once you
  confirm, the worker re-embeds every chunk and deletes the old vectors only after all new
  ones exist.
- Embeddings get no fallback on purpose, because vectors from different models can't be
  compared.
- Local `bge-small` runs through sentence-transformers. To use it, build the image with
  `INSTALL_LOCAL_EMBEDDINGS=true`, which adds about 1 GB. Ollama embeddings are the
  lighter local option.

**Budget.** Set a monthly limit in Settings → LLM. Once month-to-date spend (in your
timezone) reaches it, non-urgent calls raise `BudgetExceededError` so the job pauses. Urgent
calls, such as a deadline within the hour, still go through. You get one notification per
month. Costs come from LiteLLM's bundled price table, and local models count as $0.

**Dashboard security.** The browser calls `/api/backend/*` on the Next.js server, which
adds the API token. The token never reaches the browser. A proxy that adds credentials is
a CSRF target, so it rejects:
- cross-site requests (`Sec-Fetch-Site`)
- requests without the `X-AutoApply` header, which forces a CORS preflight that is never
  approved
- requests for unknown `Host` values, which blocks DNS rebinding

## Phase 3 design: template lock, compile, page limit

```
upload .tex/.zip ─> parse regions ─> compile base ─> (compare with Overleaf PDF) ─> store version
render request ──> sanitize region LaTeX ─> fill regions ─> verify lock ─> Tectonic ─> pages ≤ limit?
                                                                  ▲                      │ no (fit=on)
                                                                  └─ binary-search the ◄─┘
                                                                     fewest bullets to drop
```

**Regions and the template lock (rule 2).**
- Editable content sits between `%%BEGIN:NAME%%` and `%%END:NAME%%` lines inside the
  document body. Marker errors are reported with line numbers, including:
  - markers in the preamble
  - nested, duplicated or unclosed regions
  - malformed markers
- `fill()` only replaces region bodies.
- `verify_lock()` then checks that everything outside the regions is byte-identical to the
  template, before anything compiles.
- If your template has no markers yet, *Suggest markers* wraps each `\section` body and
  leaves the header locked.

**Content sanitizer.** Region LaTeX, whether generated or hand-edited, may only use:
- commands your template already uses in its regions, or defines in its preamble
- basic formatting such as `\textbf` and `\href`

Two kinds of command are always rejected:

| Kind | Examples |
|---|---|
| Dangerous | file access, shell (`\input`, `\write18`), redefinitions (`\def`, `\makeatletter`) |
| Layout-changing | `\newpage`, `\setlength`, font sizes, `\\[4pt]` |

`\vspace`/`\hspace` are allowed only with values your template already uses, so no new
spacing can appear. Tectonic also runs with `--untrusted`.

**Compile checks.** Each compile runs Tectonic in a throwaway directory, with the
template's other files (such as `.cls` files and images) copied in.
- **Page count:** read with PyMuPDF.
- **Overfull boxes:** parsed from the log and mapped back to the region that caused them.
  The alignment check fails above 1pt.
- **Text extraction:** via `pdftotext`, in the same reading order an ATS sees. PyMuPDF is
  the fallback.

**Page limit (rule 3).** A PDF over the limit is never accepted.
- With `fit` on, bullets are removed lowest-priority first.
- The removal order is fixed, so a binary search finds the fewest removals that fit, in
  about log₂(n) compiles. For example, trimming 70 bullets takes about 8 compiles at
  about 0.3 s each.
- Because it removes the minimum, the page stays as full as possible.
- Mark a bullet line with `%%prio:0.3` (lower is removed first) or `%%pin` (never removed).
  The generator in Phase 5 sets these, and pins the AutoApply signature project.
- The last bullet of an entry is never removed.
- The bullet macro is auto-detected (e.g. `\resumeItem`). You can override it by adding a
  `%%BULLET:\myItem%%` line.

**Engine compatibility.** Tectonic uses XeTeX, while Overleaf defaults to pdfLaTeX.
- Some resume templates, such as Jake's, include pdfTeX-only ATS lines:
  `\input{glyphtounicode}` and `\pdfgentounicode=1`. At compile time these become no-ops,
  through a shim loaded on the first line so TeX line numbers don't move.
- XeTeX already emits copyable Unicode text, so the ATS benefit is kept.
- Any visible engine difference shows up in the Overleaf fidelity check below.

**Overleaf fidelity.** If you upload the PDF Overleaf produced, it's compared with the
Tectonic build: page counts and word-level text similarity (at least 90% required). A
mismatch usually means a font or package differs between Overleaf and Tectonic.

**Versions.** Every render is stored as a new, immutable version of the template's lineage,
with its `.tex`, PDF, log and build report. Phase 6's Resume Studio shows the diffs between
versions.

**Docker.**
- Tectonic 0.17.0 is pinned and its SHA-256 is verified at build time.
- The package cache is pre-warmed at build time with common resume packages (`fontawesome5`,
  `titlesec`, `enumitem`, `glyphtounicode`, …). A cold first compile takes minutes; a warm
  one takes about 0.3 s.
- The warm-up layer only rebuilds when `app/latex` or `templates/` change; other code changes rebuild in seconds.
- The warmed cache lives in the image, not in a volume, so it can't be hidden by an empty volume or go stale after a rebuild. A package missing from it is downloaded on first use.

## Phase 4 design: knowledge base (RAG)

```
GitHub API ─> metadata + README + role (from contributor stats) ─> LLM summary ─> truthfulness guard ─┐
past .tex/.pdf ─> deterministic bullet parser ─> exact + near-duplicate filter ──────────────────────┼─> bullets
                                                                                                     │   + repos
                                         embed_missing(): vectors for the active model only <────────┘
query/JD ─> embed ─> nearest chunks (same model) ─> + keyword overlap + outcome boost ─> ranked hits
```

**Truthfulness (rule 1).** Content can only come from three sources: your GitHub data, your
uploaded past resumes, and your profile.
- **Role on each repo** comes from GitHub's contributor stats, not the LLM: "Sole developer",
  "Contributor (35% of commits)", or "Repository owner".
- **LLM repo summaries** are then checked by a deterministic guard. It drops any tech term
  or number that doesn't literally appear in the repo's own data (README, description,
  languages, topics, stars). Everything it removes is listed on the repo card, so nothing
  disappears silently.
- **Past resumes** are parsed without an LLM:
  - `.tex`: arguments of item-style macros (`\resumeItem{…}`) and bare `\item` text, with
    their section and entry heading.
  - `.pdf`: bullet glyphs plus wrapped continuation lines.
- **Skill tags** are literal matches against a vocabulary made of built-in terms, your
  profile skills, and your GitHub languages and topics. A tag can't be invented.

**De-duplication.**
- Exact duplicates are caught by a normalised-text hash.
- Near-duplicates are caught by embedding similarity (at least 0.93, the same bullet
  reworded). New and stored bullets are embedded in exactly the same form, so the
  comparison is fair.

**Embeddings.** Chunks are derived from the source tables. `embed_missing()` makes the
vector store match them for the active model: it embeds new items, re-embeds changed ones
and removes orphans. It runs after every ingest, every minute in the worker, and after an
embedding-model change triggers a re-index. No database transaction is held open during
LLM or embedding calls.

**Retrieval.** Results are ranked by:

```
score = cosine similarity (active model only)
      + 0.15 × share of the query's skill keywords the item has
      + 0.10 × outcome score
```

On Postgres the nearest-neighbour search uses pgvector (`<=>`). The outcome score is learned
in Phase 9 from which resumes got shortlisted.

**GitHub sync** runs in the worker, when you click Sync or every Sunday at 03:00 UTC.
- Unchanged repos aren't re-summarised: an input hash is stored per repo.
- Repos that are deleted or excluded are removed, along with their bullets and vectors.
- Private repos and forks are excluded by default.
- Without a token, GitHub allows 60 requests an hour, and a sync uses about 4 per repo.
  Add a read-only token to raise the limit.

## Phase 5 design: generation pipeline

```
analyze JD → eligibility ─(ineligible, not forced)→ END
                 └→ retrieve facts → write → evaluate ─┬─ passed or out of attempts → polish → finalize
                                        ▲              │
                                        └── feedback ◄─┘ (LaTeX errors, removed claims, overflow,
                                                          overfull lines, addable skills, low score)
```

**Orchestration.** The pipeline runs on LangGraph.
- State is checkpointed after every step in Postgres (LangGraph's `PostgresSaver`), keyed
  per run.
- If the worker crashes, the run is re-queued on restart and resumes from the last completed
  step, without redoing earlier LLM calls.
- The API only queues a `pipeline_runs` row; the worker executes it within 15 s. The job page
  shows the current step live.

**JD analyzer** (`jd_analyzer` task) extracts:
- required and preferred skills, keywords and responsibilities
- seniority
- eligibility: CGPA cutoff, branches, batches

Any skill that doesn't literally appear in the JD is dropped. Eligibility is checked against
your profile, and the result is one of:
- **eligible**
- **not eligible**: email jobs are skipped; manual jobs are generated anyway with a warning
- **can't tell**: your profile is incomplete

**Fact pack (rule 1).** The resume writer sees only facts, each with an ID:
- retrieved bullets and repo summaries (Phase 4)
- your template's current region content
- your profile
- the AutoApply signature fact

**The writer** (`resume_writer`) outputs region LaTeX in your template's style. Every
attempt then goes through, in order:

1. **Truthfulness guard.** It deletes any bullet whose numbers or skills aren't in the fact
   pack. If a heading or skills line makes an unsupported claim, the whole region reverts to
   your current content. Nothing is invented, and every removal is listed in the report.
2. **Sanitizer.** Only your template's own commands are allowed (Phase 3), so the layout
   can't change.
3. **Signature project (rule 5).** It must be present. Its bullets are `%%pin`ned, so page
   fitting never drops them.
4. **Page fit (rule 3).** Lowest-priority bullets (`%%prio`) are dropped until the page
   limit holds.
5. **ATS score** on the PDF text:

   ```
   score = 0.5 × keyword coverage   (required skills count double)
         + 0.3 × semantic similarity (embeddings)
         + 0.2 × format checks        (content intact after extraction, headings found,
                                       reading order, standard sections)
   ```

   `ats_scorer` can credit synonyms, but only with an exact quote from the resume, which is
   verified.

**Quality loop.** Up to 4 attempts, set in Settings. An attempt passes when:
- it compiles, and fits the page limit
- it has no overfull lines
- the signature project is present
- the ATS score is at or above the threshold (80 by default)

A failed attempt sends specific feedback to the writer. The best attempt is then polished
(`final_polish`) and kept only if it's no worse.

**Report.** The score breakdown, matched and missing skills, **gaps** (JD skills that aren't
in your data, which are never added), removed claims, changes versus your base resume, every
attempt, models used, and the job's total LLM cost.

## Phase 6: dashboard

| Page | What it does |
|---|---|
| `/jobs` | Tracker. Edit status and notes inline, filter by status, auto-refreshes. |
| `/jobs/new` | Manual mode: paste a JD and generate. |
| `/jobs/{id}` | Live progress, score report, eligibility, JD analysis, version picker, PDF (react-pdf). Buttons: **Approve**, **Withdraw**, **Regenerate**, **Edit in studio**, **Delete**. |
| `/studio/{id}` | Monaco LaTeX editor with region markers highlighted. ⌘/Ctrl+S saves and recompiles into a new version. Side-by-side diff against any earlier version, page-limit setting, live PDF. |
| `/settings/general` | Auto-apply (asks you to confirm before turning it on), default delay, ATS threshold, page limit, quality-loop attempts, timezone, and your profile. |

Some details:
- **The studio** only accepts saves that leave the template lock intact: a change outside
  the regions is rejected with an explanation.
- **Approve (rule 6)** records an approved application tied to the *exact* resume version you
  reviewed. The Phase 8 portal agent submits only approved applications. You can withdraw
  approval until it's submitted.
- **Notifications** appear in the sidebar: failures, budget, config problems, sync results.
- **Monaco and the PDF.js worker** are served by the app itself, not a CDN.

## Phase 7 design: email → jobs

```
inbox ─(metadata only)→ sender rule match? ─no→ never downloaded
                              │ yes
                  full MIME → parse → DMARC/DKIM not failed? → classify (email_classifier)
                              │
      job_notice → pick apply link → unwrap SafeLinks/redirects (HEAD only) → on portal allowlist?
                              │ yes                                              │ no
          create/dedupe Job → schedule (job > rule > global delay; sooner if deadline) → "suspicious" + alert
                              │ due
          Direct link: portal agent reads the JD → generate
          Read email then apply: JD from email + attachments (+ portal page) → generate
```

**Providers.** Outlook/Microsoft 365 (Microsoft Graph, OAuth with PKCE), Gmail (Gmail API,
OAuth with PKCE) and generic IMAP (app password). All three hand over the raw MIME message,
so a single parser handles every inbox.

**Privacy.** Only sender and date metadata is listed. A message is downloaded only if it
matches one of your sender rules, and only matched mail is stored or sent to the LLM.

**Rule 8 (link safety).**
- The sender must match a rule, and must not fail DMARC or DKIM.
- Outlook SafeLinks, Google redirects and urldefense links are unwrapped offline. Other
  trackers are resolved with `HEAD` requests only (at most 5 hops, no page bodies).
- The final URL must be `https`, contain no embedded credentials, and be on the linked
  portal's allowed domains.

Anything else is marked **suspicious**, you get an alert, and nothing is opened.

**Forwarding fallback.** When your college blocks access, an Outlook rule can forward Havlock
mail to Gmail. The original sender is recovered from the forwarded block, but it's trusted
only when the forwarder is one of *your own* addresses and the forward passes
authentication. Otherwise a stranger could fake a "forwarded Havlock email".

**De-duplication.**
- The same RFC Message-ID arriving in both inboxes maps to one job, and the second copy
  isn't even re-classified.
- Otherwise jobs are keyed on the portal job URL (ignoring tracking parameters), falling
  back to company + role.
- Reminders attach to the existing job. Deadline extensions update its deadline and notify
  you.

**Scheduling.** A job is processed after its delay: the job's own setting, else the rule's,
else the global default of 60 minutes. If the deadline is sooner than the delay, it's
processed immediately and you get an alert. Inboxes are polled every 5 minutes (a setting),
or on demand with **Check now**.

## Phase 8 design: portal agent (Playwright)

- **Runs.** `scrape` and `apply` runs execute in the worker using headless Chromium, with the
  portal session saved and **encrypted** (cookies are credentials).
- **Navigation guard.** Main-frame navigation, including server redirects, to any host
  outside the portal's allowed domains is **blocked before the request is sent**. The final
  URL is re-checked after every step.
- **Login.** Uses your saved portal username and password (encrypted).
  - **OTP and CAPTCHA are never bypassed (rule 6).** The agent pauses, shows you the
    screenshot on the job page, and types only what you enter. It gives up after 10 minutes,
    and the job becomes *Paused*.
- **Scrape.** Reads the JD and fields, preferring configured selectors and using
  `portal_helper` to fill gaps. It downloads PDF/DOCX attachments (allowed domains only),
  merges their text into the JD, and screenshots the page for your records.
- **Apply.** Runs only for an **approved** application, or with auto-apply on *and* the resume
  passing every check.
  1. Requires explicit apply selectors, and never guesses.
  2. Fills fields only from your profile. A missing value stops the run rather than being
     invented.
  3. Uploads the exact approved PDF.
  4. Screenshots before submitting, and again at the confirmation.
  5. Never submits twice.
- **Lifecycle for email jobs:**
  1. detected
  2. scraped (portal)
  3. resume ready
  4. **awaiting approval**
  5. Approve
  6. applied, with a confirmation screenshot

### Portal selectors (Havlock)

Edit `backend/portal_configs/havlock.yaml`. It's mounted into the containers, so there's no
rebuild. Copy stable selectors from the real pages: right-click → Inspect, and prefer
`id`, `name` or `data-*` attributes.

| Section | What to fill in |
|---|---|
| `login` | The username, password and submit fields, if the defaults don't match. |
| `job` | Optional selectors for title, company, description and deadline. Without them, the JD comes from the page text. |
| `apply` | **Required** to apply: `resume_input`, `submit`, `confirmation`, plus any `fields` mapped to profile paths (`phone`, `education.0.cgpa`, `form_fields.roll_number`). |

## Phase 9: outcome learning + analytics

**Outcome learning.**
- Each generated resume records which bullet-bank bullets it used, taken from the writer's
  cited fact IDs.
- Setting a job to **Applied / Shortlisted / Rejected / Offer** records the outcome. An
  application record is created if you applied outside the agent.
- Each bullet's score is recomputed from the resumes that used it:

  ```
  outcome_score = Σ weight / (n + 2)     weights: offer +1.0, shortlisted +0.6,
                                                  rejected −0.3, no response −0.1
  ```

  The `+ 2` shrinks scores toward zero while there's little evidence. Retrieval adds
  `0.10 × outcome_score`, so what worked before is preferred.

**`/analytics`** shows:
- applications and resumes generated per week (12 weeks)
- shortlist rate and average ATS score
- top skill gaps across jobs: what to learn or build next
- jobs by status
- LLM cost per month and per job
- which bullets are working

## Job websites (search → apply)

Open **Job websites** in the sidebar and add any job site: Havlock, a company careers page,
LinkedIn and so on. For each site you set:

| Setting | Meaning |
|---|---|
| Search result links | Search and filter on the site yourself, then paste the results page's address. |
| Job categories | The roles you want. Close variants count, e.g. "ML Engineer Intern" for "Machine Learning Intern". Empty means all roles. |
| Skip words | e.g. `senior`, `unpaid`. Postings containing them are skipped. |
| Check every | How often the site is searched (at least 15 minutes). |
| Review window | Only in *Search + apply* mode. The resume is prepared right away, and the agent then waits this long before applying. `0` means apply as soon as it's ready. |
| Max new / check | A cap on new jobs per search. Extra matches are picked up at the next check. |
| Mode | **Search + prepare resume** (you apply) or **Search + apply**. |

**Stage 1: searching** runs in the worker every minute for each site that's due.
1. Open the search pages, logging in with the portal's saved login. OTP/CAPTCHA come to
   you, as always.
2. Scroll to load results, then collect links on the site's allowed domains only.
3. Pick out the postings. Set `search.job_link` in `portal_configs/<site>.yaml` to skip
   the model for this step.
4. Filter them: skip words first, then a literal category match, then the `portal_helper`
   model for near-matches.
5. Record every posting with *why* it was kept or skipped. Pages are 1.5–3.5 s apart, to
   pace requests like a person would.

**Stage 2: applying.** Each match becomes a Job:
1. Right away, the portal agent opens the posting.
2. It reads the full JD, downloads PDF/DOCX attachments and merges their text.
3. It generates the tailored resume.
4. In **Search + apply** mode, it notifies you: "applies automatically at HH:MM unless you
   stop it".
   - **Apply now** sends it immediately.
   - **Don't apply** cancels it, including an apply that's already queued.
   - If you do nothing, it applies when the review window ends.
   - A resume that fails a quality check (page limit, ATS threshold) is never sent
     automatically; it waits for your Approve.
5. In **Search + prepare resume** mode: the job is marked *Resume ready* with the link, and
   you apply yourself.

A posting that's already tracked, e.g. from a Havlock email, isn't duplicated.

**LinkedIn, Indeed and Naukri** prohibit automated access in their terms, and using the
agent there can get your account restricted. These sites are flagged, default to search
only, and switching them to *Search + apply* requires ticking an explicit risk
acknowledgement. Many of their "Apply" buttons also lead to company websites, which the
agent won't open unless they're on an allowed domain.

## What I need from you

1. **Profile** (Settings): CGPA, branch and graduation year. Eligibility checks need them.
2. **Havlock portal** (Settings → Portals):
   - its URL and allowed domains
   - your login, if it has one (saved encrypted)
   - `apply` selectors in `backend/portal_configs/havlock.yaml`, which needs Havlock's page
     structure (screenshots or saved HTML of a job page and its apply form work)
3. **Email** (Settings → Email): add your Outlook and Gmail accounts.
   - For OAuth, create a Google Cloud and/or Azure app with redirect URI
     `http://localhost:3000/oauth/callback`, and paste the client ID and secret.
   - Or use IMAP with an app password.
   - Then add a sender rule for Havlock's notification address.
4. **Sample Havlock emails** (optional, personal details removed). They help confirm link
   extraction on real messages.
5. **A second LLM provider** (optional, e.g. a free Groq key) as the fallback, so a Gemini
   outage doesn't stall everything.

## Setup

Prerequisites: Docker Desktop (or Docker Engine + Compose v2).

```bash
cp .env.example .env
# fill in the three values:
python3 -c 'from cryptography.fernet import Fernet; print("MASTER_KEY=" + Fernet.generate_key().decode())'
python3 -c 'import secrets; print("API_TOKEN=" + secrets.token_urlsafe(32))'
python3 -c 'import secrets; print("POSTGRES_PASSWORD=" + secrets.token_urlsafe(24))'

docker compose up --build
```

- Dashboard: http://localhost:3000 (jobs, new resume, template, knowledge, settings, LLM settings in the sidebar)
- API docs: http://localhost:8000/docs (click *Authorize* and paste `API_TOKEN`)
- Health: `curl localhost:8000/health`

The `api` container runs `alembic upgrade head` on start.

**Back up `MASTER_KEY`.** If you lose it, every saved key and token becomes unreadable and you
have to enter them all again.

## Local development (without Docker)

```bash
cd backend
uv venv --python 3.11 && uv pip install -e ".[dev]"
.venv/bin/pytest            # 226 tests (incl. real Tectonic + headless Chromium when installed); SQLite + fakes, plus real-Tectonic tests when it's installed
# optional, for the real-compile tests: put the tectonic binary in .venv/bin/ (see Dockerfile for the version)
.venv/bin/ruff check app tests alembic && .venv/bin/mypy app

cd ../frontend && npm install && npm run build
```

## Rotating the master key

1. Set `MASTER_KEY_PREVIOUS=<old key>` and `MASTER_KEY=<new key>` in `.env`.
2. Run `docker compose up -d api worker`.
3. Run `docker compose exec api python -m app.cli rotate-keys` to re-encrypt every secret.
4. Clear `MASTER_KEY_PREVIOUS` and restart.

## API

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | DB connectivity (public) |
| GET / PATCH | `/api/settings` | global settings (partial update, validated, audited) |
| GET / PUT | `/api/settings/profile` | your profile: education, CGPA, links, portal form answers |
| GET | `/api/settings/secrets` | secret status, masked |
| PUT / DELETE | `/api/settings/secrets/{name}` | set/remove a secret (currently `github_token`) |
| GET | `/api/llm/catalog` | supported provider kinds and their requirements |
| GET / POST | `/api/llm/providers` | list (masked keys) / add a provider |
| PATCH / DELETE | `/api/llm/providers/{id}` | edit (blank key = keep) / delete (409 if a task uses it) |
| POST | `/api/llm/providers/{id}/test` | tiny call: success, latency, error |
| GET | `/api/llm/providers/{id}/models?embedding=` | live model list, falls back to suggestions |
| GET | `/api/llm/tasks` | per-task routing and effective params |
| PUT | `/api/llm/tasks/{task}` | set primary/fallback/params (`confirm_reindex` for embeddings) |
| GET | `/api/llm/usage` | month-to-date cost, budget, by task/model/job, recent calls |
| GET | `/api/notifications` | alerts (budget reached, re-index done, …) |
| POST | `/api/templates` | upload `.tex`/`.zip` (+ optional Overleaf PDF), compile, activate |
| POST | `/api/templates/suggest-markers` | wrap `\section` bodies with markers (not saved) |
| GET | `/api/templates`, `/api/templates/{id}` | templates with regions, build report, Overleaf check |
| POST | `/api/templates/{id}/activate` | make a template the base for generation |
| POST | `/api/resumes/render` | fill regions → compile → page/overfull checks (`fit` to auto-trim) |
| GET | `/api/resumes?lineage_id=`, `/api/resumes/{id}` | versions |
| GET | `/api/resumes/{id}/pdf` · `/tex` · `/log` | outputs |
| GET | `/api/knowledge/status` | counts, embedding coverage, sync status |
| POST | `/api/knowledge/github/sync` | queue a GitHub sync (the worker runs it) |
| GET | `/api/knowledge/github/repos` | repos with guarded summaries, bullets, dropped claims |
| POST / GET | `/api/knowledge/resumes` | upload past `.tex`/`.pdf` resumes / list them |
| DELETE | `/api/knowledge/resumes/{id}` | remove a past resume and its bullets |
| GET | `/api/knowledge/bullets?source=&q=` | bullet bank |
| PATCH / DELETE | `/api/knowledge/bullets/{id}` | switch a bullet on/off / delete it |
| POST | `/api/knowledge/search` | ranked retrieval for a query or JD |
| POST | `/api/jobs/manual` | manual mode: create a job from a pasted JD and queue generation |
| GET | `/api/jobs?status=`, `/api/jobs/{id}` | tracker list / detail (runs, resumes, applications) |
| PATCH / DELETE | `/api/jobs/{id}` | edit status/notes/company/role / delete |
| POST | `/api/jobs/{id}/regenerate` | queue another generation run |
| POST / DELETE | `/api/jobs/{id}/approve`, `/api/jobs/{id}/approval` | approve a resume version / withdraw |
| POST | `/api/resumes/{id}/edit` | studio save: full `.tex`, lock-checked, recompiled as a new version |
| POST | `/api/jobs/{id}/retry` | resume a failed run from its checkpoint |
| POST | `/api/jobs/{id}/prompt` | your OTP/CAPTCHA answer for the waiting portal agent |
| GET | `/api/jobs/{id}/files/{file_id}` | attachments and screenshots |
| GET / POST / PATCH / DELETE | `/api/email/accounts[/{id}]` | inboxes (passwords/tokens encrypted, masked) |
| POST | `/api/email/accounts/{id}/test` · `/sync` · `/oauth/start` | test, check now, connect |
| POST | `/api/email/oauth/callback` | OAuth code exchange (called by the dashboard) |
| POST / PATCH / DELETE | `/api/email/accounts/{id}/rules`, `/api/email/rules/{id}` | sender rules |
| GET | `/api/email/messages` | allowlisted emails and what happened to each |
| GET / POST / PUT / DELETE | `/api/portals[/{id}]` | portals + allowed domains |
| PUT / DELETE | `/api/portals/{id}/credentials` | portal login (encrypted, never returned) |
| GET | `/api/analytics` | weekly counts, funnel, shortlist rate, ATS, gaps, costs, learned bullets |
| POST | `/api/notifications/{id}/read`, `/read-all` | mark read |

## Using the LLM settings (first run)

1. Open http://localhost:3000/settings/llm and add a provider. Paste the key, then click
   **Test connection**.
2. Under **Model per task**, pick a provider and model for each task. Suggested setup:

   | Task | Model |
   |---|---|
   | resume_writer | your strongest model |
   | email_classifier, portal_helper | a cheap, fast model |
   | everything else | a mid-tier model |

   Add a fallback from a *different* provider, so one provider's outage doesn't stop you.
3. Pick an embedding model. Do this before Phase 4 ingests your data; changing it later
   triggers a re-index.
4. Set a monthly budget.
