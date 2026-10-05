# Filing Diff

Filing Diff compares sections of two comparable reports and keeps the evidence.
Everything is read and managed in the **Filing** page (Research menu, F20): history,
source coverage, freshness, excerpts and citations. In Markets (the security's
"Filing" tab) and in Fundamentals, a one-line summary with a link to the page
remains. The current comparison
and the historical one are kept distinct: a recent unverified document prevents
a previous pair from being presented as up to date.

## Configuration

Each security requires a curated document profile: issuer identity,
language, period, scope, sources and rules for recognizing sections. The advanced
profile (Filing page, "Profile") lets you save the profile JSON; each save creates
a revision. Recognition is not universal. SEC, ESEF and IR sources have
different coverage; ZIP files, scans without text and unhandled amendments remain
visibly excluded.

The `verifica.periodo` regex recognizes explicit dates with the groups `inizio`
and `fine`. For reports that declare a duration in months and the end date,
it can instead use `mesi` and `fine`, for example
`(?P<mesi>six)-month period ended (?P<fine>[A-Za-z]+ \d{1,2}, \d{4})`.
Durations of 3, 6, 9 or 12 months are allowed, including the corresponding
English words, consistent with the report type and ending on the last day
of the month. The computed start date and the rule are kept in the evidence together with
the original text and its hash. This is not a universal automatic recognition:
the profile regex must isolate the relevant period declaration;
ambiguous dates, conflicting catalogs and week-based calendars require explicit
dates, without falling back on the title alone or on the catalog date.

"Automatic check" (in the advanced profile) enables the profile for the worker. The default interval
is weekly; manual refresh is also possible with periodicity
turned off. The standalone worker is installed as described below; the backend
with authorized evaluation automation can also acquire the sources
through the limited check described here.

The AI judgment is a separate option that uses the API and may incur costs.
It is requested only with the security's "Check now" button: periodic runs
(the backend's hourly check, activation, refresh at the start of an Advisor
run) never call it, even with the option enabled, and declare it
as "AI judgment only from the button (Check now)". If a judgment already requested
with the button concerns the same evidence, the periodic run shows it (without AI).
It reuses the model configured in `ACTION_EXTRACTOR_MODEL`. Citations are
checked and the limited input is declared; an identical comparison already evaluated
is reused. The judgment does not automatically update fair value,
approved assumptions or orders. Source excerpts remain in the original language.

## Filing page

Research menu → Filing (key F20; the keys of other pages do not change). The
badge next to the entry counts the portfolio securities with a comparison newer
than the Committee's last run (same rule as the Advisor: it drops after the
run, not when the security is opened).

**Header.** Portfolio or Favorites (favorites outside the portfolio are
visible and are activated one at a time, but do not enter the Advisor's context),
covered securities, last check, "Daily check" switch (stops or
restarts the hourly check of expired profiles; the choice persists after a restart;
if `FILING_AUTO_REFRESH_ENABLED=false` in `.env` the switch is locked),
"Review links" (steps through the securities to confirm) and "Activate missing" (portfolio
only, free, no AI). After a bulk activation the page warns
that the first comparisons usually take 35–60 seconds and refreshes by itself.

**Context for the Committee.** Securities up to date, to be updated at the start of the
run (maximum wait 60 s, then the last comparison is declared), links
to confirm, without source, excluded; characters used out of the 14,000 budget and
omitted changes.

**Securities**, in four groups: *With news*; *To fix* (error, link
to confirm, AI proposal to review); *Up to date* (with changes, unchanged,
check in progress, awaiting the first comparison); *No source* (no free
source, no profile or link canceled, excluded). Sorted by "News" or
"A-Z".

**Detail.** Header with source, compared periods, check date,
next check, freshness and section coverage, "Profile" and "Check
now" (free; if the profile has AI judgment enabled the button says so: it is a
paid call). Three cards:
- *Changes*: "Highlights" shows the same changes, in the same order,
  that enter the Committee's context; then Risks, Management, Litigation, All.
  Each change has type, section, page, citation IDs (C<n>-before/after)
  and the link to the document; in modified ones the removed text is struck through and the added
  text highlighted. In "All", sentences made only of numbers (PDF tables
  laid out as text) are gathered under "table rows", collapsed by default,
  and displaced list numbers ("3.") are hidden with their count: this affects only
  the view, the citations and data do not change.
- *Key figures*: items of the pair (SEC XBRL or ESEF facts), with the variant if
  they come from another one (e.g. the ESEF annual report next to the IR half-year report). For inventory
  and debt an increase is marked in red.
- *What the Advisor sees*: the exact text of the security's line, with characters,
  cited changes, figures, omissions and "Copy".

In place of Changes, depending on the state: *Check in progress* (start and trigger;
the internal phases of the run are not recorded and are not shown), *The check
failed* (reason and remedy: Retry, Advanced profile, or nothing if the PDF
requires OCR), *Link to the source* (SEC or ESEF candidates to choose from,
"Confirm and activate" or "None of these", which discards them forever),
*No free source* (outcome of SEC and ESEF, "Provide the IR site" for the
AI proposal, CIK or LEI entered by hand, "Exclude from the check"), *Excluded*
("Include again"), *Profile proposed by AI* (below).

**Advanced profile** ("Profile"): profile JSON, automatic check,
interval, AI judgment, check history and the run's technical detail
(pair, candidates, coverage, freshness, AI judgment with citations). For the
automatic SEC/ESEF links there is "Cancel the link": the profile is
deactivated (not deleted), the issuer is no longer proposed and the Advisor
receives "link canceled" instead of the old comparisons; a newly
confirmed link or a saved profile reactivates it. "Activate missing"
skips unlinked securities.

Page limits: the phases of a check in progress are not visible;
favorites do not enter the context; profiles from IR PDFs have no key figures;
mixed ESEF + IR variant profiles have no light check (they are redone
in full at every expiry); the candidate search starts when a
security to fix is opened (SEC/ESEF network, free), never on page load.

**"Freshness" box (phase F).** With a valid comparison it says "up to date" or
"not up to date"; when the pair is missing it says "no comparison" (yellow)
with the short reason (e.g. "repository stuck at FY2022"), or "awaiting the first
comparison". The full reason remains in the note above the changes.

**Quarter on quarter (phase F).** A newly listed SEC issuer has no
10-Q for the same quarter of the prior year. For automatic SEC profiles on the
10-Q, if that filing does not exist at all in the catalog, the comparison is made with the
immediately preceding verified 10-Q (adjacent quarters, same duration);
the 10-K never enters this pair. This is declared by the "Comparison" box
("Q1 2026 vs Q4 2025 · quarter on quarter"), a yellow note above the
changes (seasonality may weigh in), the key figures ("previous
quarter") and the Advisor's line ("quarter on quarter (prior year
missing)", "figures vs previous quarter"). As soon as the prior-year 10-Q arrives,
it reverts on its own to year on year. If the prior-year 10-Q exists but does not
verify, there is no sequential pair: the issuer is not new.

## Automatic activation (SEC)

For securities whose issuer files with the SEC, the profile is created without JSON.
In the Filing page a security without a profile sits under "To fix" (link
to confirm) or "No source"; "Activate missing" (or `POST
/filings/activate-missing`) activates the portfolio securities in bulk. Everything is free: only public SEC sources, no AI.

The link to the SEC issuer is searched, in order, like this:
- ticker without exchange suffix;
- verified alias in the private store (`data/alias_fonti.json`, `sec` section);
- identical issuer name (normalized) in the public SEC list,
  cached for 24 hours.

It links by itself only when the result is unambiguous. Two issuers with the
same name, or an abbreviated exchange name (for example "NOVA SEMICOND."),
remain a proposal to confirm with one click. The bare ticker of a foreign
exchange is never enough: `ZZB.L` is not the US issuer `ZZB`. The profile stays saved
under the portfolio ticker (e.g. `ACME.MI`), so the Advisor finds it, and keeps
the CIK, the SEC ticker and the origin of the link. A rejected link and the securities
excluded from the check are stored in `data/filing_preferenze.json`.

The created profile contains up to two variants: annual and interim. The run
executes both and presents the most recent comparison. The check is
daily (24 hours).

| Form | Type | Sections compared |
|---|---|---|
| 10-K | annual | risk factors (1A), litigation (3), management (7 MD&A), market risk (7A) |
| 10-Q | quarterly | management (MD&A), market risk, litigation, risk factors |
| 20-F | annual | risk factors (3.D), management and outlook (5) |
| 6-K | quarterly or half-year | full text of the report |

Alongside the text, the comparison reports the changes in some key figures
for the same two periods, read from SEC companyfacts (XBRL): revenue, operating
income, net income, inventory, debt, operating cash flow. Items
missing in one of the two periods are omitted.

Declared limits:
- **20-F**: litigation (8.A.7) is not in the model because it often refers
  to a financial statement note. A 20-F laid out as an annual report (without
  "Item") may fail to have its sections recognized: the section is reported
  as "not available" with the reason and the advanced profile remains.
- **6-K**: from each filing only the financial statements document is taken. First a document
  with Inline XBRL is searched for; otherwise one with results-related words in its
  name. Notices and press releases of other kinds are excluded and counted in the coverage.
- **6-K, window**: at most 80 6-K filings from the last 800 days are examined.
- **6-K, period**: the most recent date wins, because the prior-year comparatives
  are on the same page. 52-week closings (e.g. June 27)
  are accepted.
- **6-K, same period**: if a press release and a report cover the same
  period, the longer text is compared.
- **6-K, type**: the interim variant is added after activation,
  reading the type (quarterly or half-year) from the most recent 6-K report.
  If the search fails (network, no report), the outcome goes to the
  backend log and is retried at every "Check now".
- **6-K/A**: amendments to 6-Ks are excluded and counted in the coverage.
- **6-K, document**: if a filing contains several iXBRL documents (cover and
  report), the largest is taken. Issuers that publish 6-Ks with opaque file
  names and without iXBRL have no interim variant: the annual one remains,
  and the reason is declared.
- **6-K, figures**: SEC companyfacts usually does not contain the interim periods
  of foreign issuers. When the presented variant has no figures, those of the
  most recent variant that has them (usually the annual one) are shown,
  indicating it in the `variante` field of the figures.
- **Newly filed figures**: companyfacts is cached for 7 days. If it does not
  yet contain the compared period it is re-read once; if it is still missing,
  the state is "non_aggiornato" with the reason.

The presented variant is the most recent among those with at least one section
compared: an annual report with no recognized sections does not hide a successful
half-year one. The run also keeps the comparison and the document pair of the
other variants, in `varianti`.

Activation never overwrites an existing profile, not even a hand-written
one: in that case the outcome is "gia_attivo".

The same rules can be used in hand-written profiles:
- `forme_sec`: limits the forms considered;
- `sezioni_salta_indice`: uses the body heading, not the index entries
  that repeat it;
- `sezioni_intero`: a single section with the whole text;
- `periodo_regola: "piu_recente"`: current period and comparatives on the same page;
- `stesso_periodo: "piu_lungo"`: between two documents of the same period keeps the longer text;
- `varianti`: list of variants, each with `tipo` and the keys above.

Endpoints, all requiring a session:

| Method and path | Purpose |
|---|---|
| `GET /filings/{ticker}/proposal` | proposed source and candidates |
| `POST /filings/{ticker}/activate` with optional `{"cik": "…"}` or `{"lei": "…"}` | activates the security |
| `POST /filings/activate-missing` | activates the portfolio securities in bulk |
| `POST /filings/{ticker}/exclude` with `{"escluso": true}` | excludes or re-includes a security |

The first comparison starts in the background right after activation.

## Automatic activation (ESEF)

For European securities with no SEC link, the profile is built from the annual
ESEF financial statements published on filings.xbrl.org. The entry points are
the same as for the SEC: the "Source link" card on the Filing page, "Activate
missing" and `POST /filings/activate-missing`. Free, no AI.

**Link (LEI).** The LEI is looked up only if the SEC does not give a unique
issuer: US securities never query filings.xbrl.org. Order:
- the LEI in the private store `data/lei_emittenti.json`;
- an issuer name identical to exactly one entity in the repository. Only the
  spellings of the legal form are normalised ("S.p.A." = "SPA" = "Società per
  azioni", "AG" = "Aktiengesellschaft", "N.V." = "NV"); words such as "Holding"
  or "Group" count, because a holding company and its listed subsidiary have
  different LEIs.

The link is made automatically only in these two cases. The following remain a
proposal to confirm with one click: two entities with the same name; a single
result with a different name (e.g. "Nova Holding S.p.A." versus "Nova S.p.A.");
a full first page of results (10), because a namesake could be beyond it.
Confirmation is done with `POST /filings/{ticker}/activate` and `{"lei": "…"}`.
More than 5 similar results with no identical name: no proposal, the LEI is
needed in the store. If the LEI store is unreadable, activation stops with an
error: it might contain a different LEI from the one found by name. If the SEC
is in error (network, `SEC_CONTACT_EMAIL` missing), there is no fallback to
ESEF: the security might have a SEC link. If the SEC result is ambiguous, the
CIK is confirmed first. The profile stays saved under the portfolio ticker and
keeps the LEI and the origin of the link.

**What is compared.** The run reads the filing's xBRL-JSON and uses the notes to
the financial statements labelled as IFRS text blocks (`ifrs-full:…TextBlock` /
`…Explanatory`), without dimensions and for the current financial year. Each
note is a section, compared sentence by sentence with the usual engine. Section
names are in Italian for the main notes ("rischi finanziari", "rischio di
credito", "contenziosi e passivita' potenziali", "gestione: stime e giudizi",
"gestione: eventi successivi", …). The other sections have a name derived from
the concept ("nota: leases", "principio: revenue"). A note that appears in only
one year shows up as added or removed text.

- **Tables**: text inside the notes' tables and rows made up almost entirely of
  numbers (at least 4 numbers, and numbers making up at least half of the
  words) are left out of the comparison: the key numbers come from the XBRL
  facts. Tables laid out as text by PDF converters may remain; in the context
  they weigh less than prose (see "Score").
- **Repeated sentences**: issuers tag the same text under several concepts
  (credit risk inside financial risk management, repeated accounting
  policies). Each repeated sentence is kept only in the most specific note, so
  a note restructured from one year to the next does not become "added" text. A
  note made entirely of sentences already present elsewhere is not compared,
  with the reason given.
- **Per-document cap**: 1,500,000 characters and about 8,000 sentences, filled
  in priority order: risks, litigation, estimates and events, other relevant
  notes (segments, goodwill, capital), other notes, accounting policies. Notes
  beyond the cap show as "not available: over the document limit" (this happens
  with banks, which have millions of characters of notes).
- **Language**: English if available in the two most recent financial years,
  otherwise the language common to the two (usually the original). It is
  re-evaluated on every run: a profile born in Italian switches to English when
  English becomes available. The file name suffix (`…-en.xhtml`) counts as the
  language only if the issuer has at least one financial year with versions
  under different suffixes. Otherwise the language declared in the filing's
  facts decides: in some filings `-IT` indicates the country.
- **Pair**: the latest verified financial year and the one 12 months earlier.
  Identity (the LEI in the facts), period (annual duration of the text blocks,
  equal to the catalogue date) and language are proven on the filing's facts.
- **Duplicate uploads**: the same financial year uploaded more than once in the
  same language counts only once (the latest upload wins), as stated in the
  coverage.
- **Download**: only the latest 3 financial years. The xBRL-JSON files (from a
  few MB to tens of MB for banks) stay in the security's archive and are not
  downloaded again as long as the archived file has the same hash.

**Key numbers.** Same items as the SEC (revenue, operating income, net income,
inventories, debt, operating cash flow), from the `ifrs-full` facts without
dimensions of the two filings. The comparative in the most recent filing wins.
The source is "ESEF xBRL-JSON (ifrs-full)". Banks have no "revenue": the items
present are kept.

**Pacing and cache.** filings.xbrl.org does not publish a limit. At most one
request per second is made, with the pacing shared between the backend and the
Advisor (`.esef_ritmo` in `data/`). Every request carries the contact
`SEC_CONTACT_EMAIL`. The index of a LEI's filings stays cached for 6 hours in
`data/esef_cache/`. An unreadable cache is re-read immediately. With the network
down, the previous copy is used and this is stated: the run is "partial" and
should be retried.

**Context.** The security's line in the Advisor's context gives the source as
`ESEF LEI …` and the financial year ("year to 31/12/2025 vs 31/12/2024"). If the
full run finished without a pair (e.g. repository stalled), the line says "no
comparison:" with the reason, even after a light check.

**SEC links that do not count.** For a security on a foreign exchange, a
"unique" SEC issuer that does not file 10-K, 10-Q, 20-F or 6-K (unlisted ADRs,
exemption 12g3-2(b)) is not a source: the LEI is looked up. The same applies
when the SEC finds only similar names (e.g. "Nova Chile" for "Nova S.p.A.") and
the LEI has an identical name: ESEF is activated. If there is also more than one
LEI, ESEF is proposed for confirmation. The fallback to ESEF applies only to
foreign exchanges (tickers with a suffix): US securities never query
filings.xbrl.org. In the proposal, the `preferita` field (`sec`, `esef` or
empty) says which source would be activated.

Declared limits:
- **Annual only**: European half-year and quarterly reports are not in ESEF
  xBRL: they are added as a variant with the "AI proposal from IR PDF" (button
  only).
- **Management report**: it is not tagged in xBRL; only the notes to the
  financial statements are compared.
- **Block tagging from FY2022**: before then the text blocks are usually
  missing; the filing shows as "no IFRS text block".
- **Non-exhaustive repository**: filings.xbrl.org collects filings from national
  registers, but not all of them. As of October 2026 it has no German filings:
  for example, a Frankfurt issuer shows as "no source". Some issuers are stuck
  at old financial years, and some filings have no xBRL-JSON (stated in the
  coverage). If the latest financial year in the repository closed more than 18
  months ago, the comparison is historical (not current) and "partial", with the
  reason "repository stalled at FY…: non-current comparison". The fingerprint of
  the light check includes the repository's latest financial year (even without
  JSON) and the "stalled" state: when they change, the full run is redone.
- **Hand-written ESEF profiles**: without `esef_modo: "blocchi"` they stay on the
  HTML report with the profile's sections, as before.
- **Stalled or absent repository**: from phase F, the official packages are
  looked for on the issuer's website (next section).

## Sources from the issuer's website (phase F)

When filings.xbrl.org does not have the expected financial year, or does not
know the issuer, Bellomberg searches the company's website on its own, free of
charge and without AI:

- **Official ESEF package.** `.xbri`/`.zip` packages with the official name
  `LEI-YYYY-MM-DD-n[-language]` found on IR pages are downloaded (at most 80 MB)
  and converted locally into xBRL-JSON (own Inline XBRL converter, `ixbrl_oim`:
  XHTML at most 200 MB, zips with `../` paths or a suspicious compression ratio
  rejected). The LEI and the period are verified on the facts as for the
  repository: a package whose LEI or period differs from the name is discarded
  with the reason. For the same financial year the repository wins. The coverage
  states it ("FY2025, FY2024 from the issuer's website…") and the Advisor's line
  says "ESEF … from the issuer's website".
- **LEI from GLEIF.** If filings.xbrl.org does not have the issuer, the LEI is
  looked up by name on the public GLEIF API (only active entities from countries
  with an ESEF obligation and the United Kingdom, identical name; otherwise
  confirmation as today). An issuer absent from the repository counts as "no
  filing", not as an error.
- **IR PDFs found.** For securities still "without source", the most recent
  periodic document is chosen among the site's PDFs (annual, half-year,
  quarterly or MD&A; presentations, transcripts, notices, summaries and the like
  excluded) together with the same document from the year before. The page
  shows them in the "No free source" card: "Estimate cost (free)" fills in the
  form and downloads the PDF for the estimate; the AI proposal starts **only**
  from "Propose with AI · max €…". The security stays in "Without source" until
  the proposal is saved. With no PDFs found there is "Search the site for PDFs
  (free)".

Polite, limited network use: HTTPS only, only the registrable domain of the site
(subdomains allowed, e.g. `group.<company>.com`), robots.txt respected, at most
12 HTML pages per security (3 MB each), a one-second pause, no JavaScript (file
addresses written in the page are also read). Pages are chosen most promising
first: investor and financial-statement words in the last part of the address
and in the link text; press releases, products, shareholder meetings,
sustainability and the like excluded; in archives by year only the latest three
financial years. The site comes from yfinance (`website`, for `.FRA` tickers via
the app's aliases).

When: in the daily check (before the runs) for ESEF profiles with a stalled
repository and for securities "without source", at activation and on request
("Search the site for PDFs", at most once an hour per security). **Never during
the Advisor's run**: the pipeline and the start-of-run update read only the cache
(`DATA_DIR/filing_sito/`, 7 days). The PDFs for the AI proposal are downloaded
up to 40 MB at most and the `filing_archive/ai/` folder is cleaned up (30 days,
1 GB).

Declared limits: sites differ greatly from one another; pages built with
JavaScript or archives behind forms are not found (the PDF specified by hand or
"Other PDFs" remains). No guarantee of coverage, only of correctness (LEI and
period verified). Germany: the official packages are in the register, without
free access; for a German issuer the IR PDF remains. Converter: fractions,
footnotes and Inline XBRL tuples are not read (counted in the conversion
limits).

## AI proposal from IR PDF

For securities that neither the SEC nor ESEF cover (for example a German issuer,
absent from filings.xbrl.org) and for European half-year and quarterly reports
(ESEF has only annual ones) the profile can be proposed by the AI starting from
a PDF on the investor relations page. It starts only from the button: never from
the backend check, the Advisor's run, bulk activation or the worker.

**On the Filing page.** The "AI-proposed profile" card opens with "Specify the IR
site" on securities with no free source and with "Interim report from IR PDF"
(header) on ESEF profiles; a proposal already made and not yet saved puts the
security under "To fix" with the status "AI proposal" and is re-read for free
from the archive. You specify:
- the URL of the PDF to analyse (a half-year, quarterly or annual report);
- optionally, up to 4 other IR URLs: the PDF from the year before and/or the
  results page.

Then:
1. "Estimate cost" (free) downloads the PDF, extracts the model's input locally
   and shows pages, estimated tokens and maximum cost.
2. "Propose with AI" makes **one** call to the `NEWS_SUMMARY_MODEL` model. The
   actual cost is recorded in `llm_usage` with the `filing_profile_ai` agent.
3. The outcome shows the verified period, the verified sections and the
   discarded ones with the reason, and the actual cost.
4. "Save profile" (or "Add as variant" on ESEF profiles) saves only the verified
   sections.

Nothing enters the Advisor's context before saving. Without `NEWS_SUMMARY_MODEL`
(or without `OPENROUTER_API_KEY`) in `.env`, the estimate and the proposal show
as "not configured", with no download and no cost. If you change the URL or the
other URLs after the proposal, the outcome disappears: only what has been
verified is saved.

**What the model sees.** Only the table of contents (the pages with "Contents",
"Indice", "Inhalt"…, or the first two pages with text) and the short lines that
may be headings, with the page. It does not see prose, table rows or the headers
repeated at the top or bottom of pages. The cap is 30,000 characters (about
10,000 tokens); lines beyond the cap are removed from the end and the number is
stated. The PDF's text enters the prompt as data, never as instructions. The
model replies in JSON with type, language, sections (`inizio`/`fine`) and three
verification rules: period, issuer and a sentence that proves the type of
report.

**Cost.** The estimate is an upper bound. Input tokens are characters divided by
2.5: in the real test, 2.5 to 2.9 characters per token were measured, with
Italian being denser. Output is counted at the maximum expected (2,500 tokens).
Rates come from the in-house price list or, for OpenRouter models, from their
public catalogue. In the real test the estimates were between €0.004 and €0.006
and the actual costs between €0.0006 and €0.0017 per document. The proposal
stays cached by sha256 of the PDF (`data/filing_ai/`): the same PDF is not sent
to the model twice. The only exception: a paid but unreadable response, which
stays recorded with its cost and is repeated only with the "Retry (new cost)"
button. If the local verification fails after the call, the proposal is kept
anyway, not savable and with the reason. The same proposal opened from another
security is re-verified locally with that security's name, without AI.

**Local verification.** Each regex is tested on the PDF with the same functions
as the pipeline. A section is verified only if:
- `inizio` and `fine` match a whole line (case ignored);
- the start is unique. Table-of-contents entries and chapter divider pages,
  followed immediately by the end with no text in between, do not count;
- the end comes after the start and there are at least 50 characters in between;
- it does not overlap another section;
- neither of the two regexes matches a header or footer repeated on 3 or more
  pages.

Regexes with nested quantifiers (for example `(\w+\s?)+`) are rejected before
being tested: on a normal line they would take hours, even in the scheduled
check of later years. The other sections are discarded with the reason.
Examples: "final heading missing", "matches a page heading repeated on 15
pages", "start: 2 lines match, none followed by the end with text in between".

**Verification rules.** Issuer, type and period use the model's rule if it holds
up on the document, otherwise a fallback rule. The fallback is stated in the
warnings:
- issuer: the security's name as a whole word, tolerant of "&", "and" and
  hyphens;
- type: words for the type of report in several languages;
- period: common forms, for example "six months ended 31 March 2026", "dal 1
  gennaio 2026 al 30 giugno 2026", "sei mesi chiusi al", "Half-Year Financial
  Report as of 30 June 2026", "relazione semestrale al 30 giugno 2026".

Reports that declare only the end date derive the duration from the word
"semestrale" or "half-year". The most recent end date is always chosen, because
the prior-year comparatives are in the same document. Without a verified period,
nothing is saved.

**Other PDFs.** The other URLs that are PDFs (for example the same report from
the year before) do not go to the model: they serve to choose rules that hold up
on all the documents and to say which sections do not hold up elsewhere
("sections that do not hold: related parties"). HTML pages are only IR pages,
not verified. If the other URLs change, or if the local verification rules have
been updated, the cached proposal is re-verified locally, without AI.

**Saving.** The profile is a normal IR profile under the portfolio ticker:
- sources `["ir"]`, `ir_urls` equal to the analysed PDF plus the other URLs;
- `sezioni_salta_indice` enabled and period "most recent";
- `origine_collegamento: "proposta_ai"` and the proposal's trace (sha256, model,
  date);
- weekly check: every IR run downloads the specified documents again.

An existing profile is not overwritten without confirmation: the response is 409
until `{"sostituisci": true}` is sent. Acceptance also refuses:
- with 409, a proposal made for another security or verified with earlier rules
  (it is reopened at no cost);
- with 422, `ir_urls` that do not contain the verified PDF. On an ESEF block
profile the interim proposal is added as a variant (`{"aggiungi_variante": true}`):
- the ESEF annual stays identical, and an IR variant of the same type already
  present is replaced;
- the key numbers come from ESEF and are marked as "annual variant";
- the light check no longer applies to that security, which always does the full
  run.

A changed profile makes the scheduled check due immediately, so the first
comparison starts without waiting for the interval. The AI judgement remains
exclusive to the "Check now" button. In the Advisor's context the source is `IR
<host> (AI proposal)`.

Endpoints (session required):
- `GET /filings/{t}/ai-estimate?url=…`: measurements and maximum cost, no AI;
- `POST /filings/{t}/ai-proposal` with `{"url", "altri_url"?}`: the only AI
  call;
- `POST /filings/{t}/ai-proposal/accept` with `{"sha256", "ir_urls"?,
  "sostituisci"? | "aggiungi_variante"?}`. The proposal is re-read from the
  cache; sections sent by the client are rejected.

URLs must be public http(s), without credentials. The download accepts only the
host of the specified URL: a redirect to another host fails, and the final URL
must be pasted.

Declared limits:
- **Coverage**: only the PDFs and IR pages specified. No search on the site, and
  redirects to other hosts are not followed.
- **Following year**: the proposed regexes may not hold up on the following
  year's document. Layout, headings and words split by text extraction ("JU NE",
  "202 6") change. This is why it is advisable to specify the PDF from the year
  before. The run reports the sections not found.
- **Period**: it is guaranteed only by the verification rules. A report that
  declares neither the duration nor the start date is not saved.
- **PDFs without text** (scans or vectorised text, even partial): the proposal
  rejects them with "OCR required". In the pipeline the same message explains
  the reason when a PDF with almost no text (at least 5 pages and over half) does
  not verify; a PDF that verifies anyway remains valid.
- **Size**: downloads have no size cap (existing downloader). Copies stay in the
  filing archive (`ai/`) with no automatic cleanup.
- **Key numbers**: none for IR profiles, except the ESEF variant.
- **Tables**: tables laid out as text become sentences of numbers and may appear
  among the changes.
- **Divider pages**: a divider page followed by other headings may be mistaken
  for the body of a section, when the start heading repeats identically.
- **Cost**: borne by the user, shown before and after.

## Context for the Advisor and refreshing

### Refreshing in the backend

With the backend running, a check starts at launch and then every hour. Each
check first closes orphaned runs, then executes the enabled and due profiles
(up to four tickers in parallel), except for tickers excluded from the check,
which are never refreshed, not even at the start of an Advisor run (if
preferences are unreadable no ticker is excluded and the log says so). A profile is due when
`interval_hours` have passed since the start of its last run (24 hours for
automatic profiles; from 2 hours up after a failed run).
Launch, the hourly check and activation all go through the same handler: only
one job at a time ("Check now" on a ticker remains a direct run, and a run
already active on the same ticker is not duplicated). An activation that arrives while the
handler is working does not wait for the next hour: the first comparisons start as soon as
the current job finishes. Requests to the SEC respect a pace
shared by all processes (backend, worker, Advisor runs).

`FILING_AUTO_REFRESH_ENABLED=0` (or `false`, `no`, `off`, `disabled`) in the
`.env` turns off the launch check and the hourly check; without the variable the check is
on and can be turned off or on again with the "Daily check" switch
on the Filing page (the choice is saved in `filing_preferenze.json` and read at launch;
turning it back on immediately starts a check of the due profiles, free of charge). Turning it
off from the `.env` takes precedence over the switch. Activating tickers, a user
action, still starts the first comparisons.

**Light check.** For profiles with a SEC-only source, the periodic run first reads
the issuer's list of filings (one request) and, for each variant, the most recent
candidate filing with the same rules as the pipeline. For 6-Ks, only a filing that
contains the financial statements document counts: the indexes of only the 6-Ks that arrived after
the last one examined are read, at most 80. If the filings are the same as in the last complete
run, the run closes as "checked, no new filing", without downloading
documents and without a new comparison; the Filing page and the Advisor keep showing the last
complete comparison. The complete run happens instead if:
- there is a new filing in a variant;
- the profile has changed (new version);
- the last complete run is not a valid reference: errors and interruptions
  (source in error, index of a 6-K not read, download or verification interrupted,
  in any variant) never count; a run that is not `ok` only because of stable limits
  counts for 7 days. They are stable because, with the same filings, the complete
  run would give the same outcome, for example a limited search index, a more recent /A
  amendment excluded from the comparison, the per-run document limit, or a variant that is incomplete for the same reasons;
- the SEC list or the index of a 6-K cannot be read (transient error).

ESEF profiles created automatically have the same light check: the fingerprint
is the identifiers of the filings that the run would use (see "Automatic activation (ESEF)"). If the ESEF index cannot be read, or it is only the
previous copy because the network is down, the complete run is done. Manually written profiles with IR or ESEF sources always do the complete run.

**Orphaned runs.** A run left "queued" or "in progress" for more than 2 hours belongs to a
terminated process: the next check closes it as an error with the reason
"orphaned run over 2 h (process terminated)".

**Failed runs.** After a failed run, including recovered orphans, the
profile becomes due again 2 hours after the start of that run; after consecutive
errors the wait doubles (2, 4, 8, 16 hours), never beyond the profile's interval:
a recovered orphan is therefore immediately due, and a persistent error (for
example a missing `SEC_CONTACT_EMAIL`) is retried at most four times in the
first day, then once a day. The new attempt starts from the light check: the
reference is the last complete run without errors, so with no new filings nothing is downloaded again. A failed AI judgment is never retried
automatically: only "Check now" requests it again. Until the new
attempt, the Advisor card
states "NOT UP TO DATE: last check failed"; "Check now" repeats it
immediately. Successful runs, partial runs and light checks keep the 24 hours.

### Refreshing at the start of a run

When an Advisor run starts, the due profiles of the tickers in the portfolio
are refreshed in the background while the run continues. The filing step waits at
most up to 60 seconds from the start of the run (not from the start of the wait); a
run already started by the backend is awaited, without launching a second one. Beyond the
limit the card uses the last comparison and says so: "NOT UP TO DATE:
refresh over 60 s (in progress)". A failed check is declared as
"NOT UP TO DATE: check failed: …". The unfinished run continues and
its outcome will be in the next run; at the end of the run, tickers that have not yet started are
cancelled (the backend check picks them up). No AI call: the qualitative
judgment is skipped even on profiles that have it enabled.

### Context format

Each ticker in the portfolio has a card. The first line is the status:

```text
NOVA · SEC 10-K/10-Q CIK 0009990001 · year to 31/12/2026 vs 31/12/2025 · comparison of 02/10 · run 123 · up to date (checked on 03/10, no new filing) · NEW
```

It contains the ticker, the source (SEC forms and CIK, ESEF LEI, or "manual profile"),
the periods compared, the day and run number of the comparison (the same in the
references to `get_filing_changes`) and freshness: "up to date",
"NOT UP TO DATE: …" with the reason, or "historical comparison" if the latest
document is not verified. A partial comparison names the sections not
compared ("partial comparison: not compared: management", or "no
section compared"): zero changes does not mean unchanged text.
"NEW" marks a comparison completed after the last
committee run on a document pair different from the one the committee
had already seen: the same comparison recomputed (weekly redo,
/A amendment, changed profile) is not new. Without a comparison the line says why: no profile
(can be activated from the Filing page), excluded from the check, awaiting the first
comparison, or the reason for the error.

Next come the key figures line (`figures revenue +12.0% · net income −3.1%`,
or "figures unavailable: …" with the reason) and the changes, one per line,
with a citation:

| Symbol | Change | Excerpt |
|---|---|---|
| `+` | added | new text, up to 220 characters, `[C3-after]` |
| `−` | removed | deleted text, up to 220 characters, `[C4-before]` |
| `~` | modified | before and after around the first differing point, `[C1-before, C1-after]` |
| `↔` | moved | text, up to 220 characters |

Omitted changes are declared at the end of the card:
"7 more changes omitted → get_filing_changes(NOVA, run_id=123, ordine="punteggio",
da=3, max_changes=20)": `da` restarts from the first one not shown, in order of importance,
and `run_id` reads exactly the comparison in the context even if a new one has arrived in the meantime.
A variant not presented (for example the half-year report alongside the annual one) has
a line with periods, number of changes and the reference
`get_filing_changes(NOVA, run_id=123, variante="semestrale")`.

**Order.** First the tickers with NEW and changes, then the others with
changes, then comparisons without changes, finally tickers without a
comparison; within the same group, total weight of changes descending, then
ticker. For each ticker, up to 4 changes are shown first if there is a NEW,
otherwise 2 (the base); then, if space remains, the budget is filled (see below).

**Score of a change** = section weight × type × min(1,
length/400), × prose for ESEF texts only. Weight: risk factors 3; management (MD&A) and legal proceedings 2;
other sections, including market risk, 1. Type: added or removed 1;
modified or moved 1 − similarity between before and after (a nearly
identical paragraph weighs little). Prose (only citations from ESEF xBRL-JSON): from 0.1 to 1 according to
the share of lowercase words over total tokens. It is 1 from 50% up, even
with figures in the text; table rows laid out as text (headings and
figures of financial statements converted from PDF) are worth much less. For the SEC the score
does not change.

**Budget.** The context stays within 14,000 characters. If the base fits, the
remaining space is filled in rounds: in each round every ticker can add at
most 4 more changes (NEW) or 2, chosen one at a time among all tickers
by score (on ties: group, ticker order, position in the document);
when a round finishes with space left, another is opened. At the first change that
does not fit it stops (no search for shorter lines), so a ticker with
hundreds of changes does not take space from the others and the same archive always gives
the same text. Per ticker the lines stay in score order and the
`da` reference restarts from the first one not shown. If instead the base does not fit,
changes are removed from the bottom (last ticker, lowest score), then the
figures lines from the bottom. Status lines, citations of the changes shown and references
always stay. The last line declares truncations and exact length:
"TRUNCATIONS: 1,209 changes omitted across 3 tickers; 13,842/14,000 characters.".
With a very large portfolio, if even the status lines alone exceed the
budget, the line adds "Status lines only: over budget.".

### Tool and preview

The desks read the rest with `get_filing_changes(ticker, run_id=, da=, variante=,
max_changes=, ordine=)`: `run_id` (optional) is the run of the comparison cited in the
context, which must belong to the ticker, be completed and have a comparison (otherwise the
response is a declared error); if a more recent comparison exists the response
indicates it in `latest_run_id`; without `run_id` the latest comparison applies. `ordine` is `documento` (default, by position)
or `punteggio` (same score as the context, on ties the position);
`da` is the first change shown (C<n> by position, or the rank with
`punteggio`; from 1); citation IDs stay C<n> in every case;
`max_changes` is the page size (from 1 to 20, default 5), `variante`
one of `annuale`, `semestrale`, `trimestrale`. Excerpts are limited to
220 characters; the full text is in `/filings/runs/{run_id}`. If after the last
comparison there was a light check, the response reports it in
`last_check`; a failed run after the comparison shown is in
`last_error` (date and reason), while the comparison remains the one shown in the
card, on the Filing page and in the overview (same selection: the latest completed run
with a comparison). The card declares it after a light check ("up to date
(checked on 03/10, no new filing; last error on 02/10: …)") and the
Filing page puts the ticker in the "error" state with the reason and the remedy. `diff_limits` reports the comparison limits and `unpaired_changes`
is `true` when the modified items were not paired. The tool reads
only the archive: no acquisition.

| Method and path | Purpose |
|---|---|
| `GET /filings?ambito=portafoglio\|preferiti` | list for the Filing page: UI status, document, changes, run in progress, error, last activation, pending AI proposal, coverage, context budget, daily check |
| `GET /filings/novita` | number of tickers with news (menu badge) |
| `PUT /filings/auto-refresh` | turns the daily check on or off (`{"attivo": bool}`; 409 if turned off by `FILING_AUTO_REFRESH_ENABLED`) |
| `POST /filings/{ticker}/reject` | discards SEC (`cik`) or ESEF (`lei`) candidates: they are no longer proposed |
| `POST /filings/{ticker}/unlink` | cancels an automatic link: profile deactivated (not deleted), issuer discarded |
| `GET /filings/{ticker}/ai-proposal` | latest AI proposal in the archive, without download or model call |
| `POST /filings/{ticker}/ai-proposal/discard` | the proposal is no longer "pending" (it stays in the archive) |
| `GET /filings/{ticker}/context-preview` | the ticker's card as the Advisor reads it, with `in_evidenza` (IDs of the changes in score order) |

The preview computes freshness from the archive (latest completed run within
the profile's interval); in the run it depends on the refresh at the start of the run.

On the Advisor page, the "Filing for the Committee" panel summarizes
coverage (tickers with a comparison, up to date, not up to date, without a profile,
excluded), the characters used out of the budget and the omitted lines, and offers "Activate the missing ones"
(not available during a run or without tickers to activate). Each line
opens the Filing page on that ticker; "Open the Filing page" opens the list.

Declared limits:
- the 60 s wait is not enough for the first round after a bulk
  activation of many tickers: those tickers show as "NOT UP TO DATE" in the run in progress;
- with the backend off there are no hourly checks; the refresh at the start of a run
  remains;
- the light check trusts the SEC list: a document replaced under
  the same accession is not re-read until the next complete run;
- excerpts of modified items show a window around the first differing
  point: other differences in the same paragraph are in the full text;
- reworded paragraphs are paired as "modified" within the stretches between
  identical text; a very long stretch with no identical text (over 10,000 possible
  pairs) uses a local window of ±25 segments, and beyond 30,000 comparisons per
  document the windows drop to ±3 segments (cost limited even for huge
  documents): some reworded paragraphs may remain a removed and an added item. The status line declares it
  ("removed and added items not paired (too many segments)"), as for comparisons
  recorded before this rule; in that case a `−` must not be read as
  risk eliminated without checking the `+`;
- headers and footers stay out of the text of the changes (the archived
  document does not change): in PDFs, lines that are identical apart from the numbers,
  at the top or bottom of at least 3 pages; in SEC HTML, which has no page
  boundaries, only "Table of Contents" with the page number next to it (for
  example "45 | 2025 Q3 10-Q"). A paragraph split by a header becomes
  two segments. The rule is cautious: a line with more than one group of digits
  besides the page number stays in the text, so headers with the period
  ("Q2 2025 Interim Report 14") stay, like the totals rows of tables;
  and in HTML, headers such as "Form 10-Q" or the company name repeated on every page also stay;
- no automatic update of fair value or assumptions: the filing remains a
  documentary signal.

## Archive and automation

A new database already includes the tables. For a pre-existing database, from the
project root:

```powershell
python tools/migrations/migra_filing.py --dry-run
# Close the backend before the next command.
python tools/migrations/migra_filing.py --apply
```

The migration creates a backup and verifies that pre-existing data and schema
are unchanged. SQLite is the authoritative archive; Chroma is a derived index.
An indexing error remains visible without deleting the comparison.

The portable worker reads only enabled and due profiles:

```powershell
python -m bellomberg.market_data.filing_worker --status
python -m bellomberg.market_data.filing_worker --due
```

With the `filing_diff` trigger authorized for valuations, the backend also examines
one enabled and due profile per cycle, across portfolio and watchlist.
It reuses the intervals, locks against concurrent runs and verifications of Filing Diff.
This acquisition explicitly skips AI judgment and indexing; the standalone worker
keeps its own options; `--ticker` never runs the AI judgment and
does nothing on a profile that is not yet due or has the scheduled check turned off. Verified outcomes
go through the normal event check before a valuation is queued.
Missing/disabled profiles and jobs already active remain visible in the automation
status. A generic `6-K` is not enough: the document must satisfy the configured
accounting profile. Guidance press releases outside those rules remain
outside coverage. With the backend off, this scan does not happen.

On Windows, the following program first shows the configuration; `-Apply`
registers only `Bellomberg-FilingDiff`, without modifying the other tasks:

```powershell
powershell -NoProfile -File tools/ops/windows/install_filing_scheduler.ps1
powershell -NoProfile -File tools/ops/windows/install_filing_scheduler.ps1 -Apply
```

The check runs every day at 08:10, when the user is logged in, and
at the next opportunity if the computer was off. The due dates of the individual
profiles remain in the database. On other systems, schedule the same `--due`
command with the system scheduler. Outcomes are in `filing_worker.jsonl`
in the data directory; a non-zero status requires attention.

If a process terminates after queuing a job, that job remains visible
as active. After verifying that no worker is still running it, close it
explicitly before retrying:

```powershell
python -m bellomberg.market_data.filing_worker --recover-run ID --reason "interrupted process verified"
```

The agents consult `get_filing_changes` without starting acquisitions or AI
calls. The committee also receives the available context, with limits and missing data
declared. No absence of results is replaced by an invented analysis.

The model preparer reuses verified documents and reconciles SEC references
on the same URL and the same filing date. It keeps issuer, form and
accession, with provenance and byte hash; mismatching metadata is excluded
from reuse. A `6-K` enters the financial catalog only if Filing Diff has already
verified its financial statements, issuer and period: the SEC type alone is not enough.

If the SEC tags do not cover a filing, the preparer also tries the HTML tables
of the consolidated statements. The reader supports explicit English headings,
periods in calendar months and amounts in thousands/millions of USD, EUR or GBP.
It keeps cells, columns, period, currency and hash; the compiler rebuilds the
observations from the acquired package. It does not create XBRL tags. Duplicate tables, ambiguous
columns, percentages and unsupported formats have a declared outcome. Empty
cells and dashes do not become zero; the closing balances of the cash flow statement do not become
flows for the period. The gaps in the original SEC catalog also remain visible.

When the last item of a balance sheet section sits alongside the subtotal,
the reader distinguishes the amounts only with consistent columns and an exact sum of
all the explicit items in the section. It also keeps the cells and terms of the
reconciliation. A missing value, a mismatched column or a different sum
leave the row ambiguous; the subtotal does not replace the value of the item.

These observations can document annual revenue or the annual + current
half-year - prior half-year bridge, keeping the checks on issuer,
currency and intervals. The recognized operating components alone do not attest
the completeness of working capital. Listed class, economic assumptions and the full
model still require their respective verifications.

For a foreign ordinary class, the preparer can link the local symbol
to the provider's listing when the initial financial statements declare a single
class and direct trading venue, and the latest annual report of the same issuer
defines the ordinary shares and the symbol. The reader supports explicit English
declarations for the Italian market and the `.MI` suffix documented by
[Yahoo](https://help.yahoo.com/kb/SLN2310.html). It also requires issuer, symbol,
market, type and currency to agree in the provider's response. It keeps phrases,
hashes, dates and document identities, rechecked by the compiler. The provider's
metadata reports the observation date and does not certify the historical number of
shares. ADRs, multiple classes, other markets and conflicting evidence remain
incomplete: no conversion is inferred from the ticker suffix alone.

When financial currency and listing currency are explicit and different, the
preparer also requires the exchange rate on the date of the initial price. For
pairs with EUR it acquires the daily ECB reference and keeps its original
CSV, the acquisition date and the direction. Any reciprocal is
computed and rechecked by the compiler. The ECB reference is not a transaction
price or the exchange rate at the time of the stock's close. Missing days, undeclared currencies,
subunits and unsupported pairs remain explicit;
no earlier exchange rate is carried forward. The automatic queue also
passes the financial currency observed in the profile to the shared service.

For foreign issuers the dossier also includes recent SEC `6-K` reports
and their possible `EX-99` attachments, with CIK, accession, filing date and
verified bytes. The report may contain results, outlook or other communications:
the collection does not automatically classify it as numeric guidance. The selection
limits and excluded sources remain visible. If a report is already in the
financial dossier, it is reused only with matching text and provenance;
the original evidence is not replaced by the supplementary collection's metadata.

For the supported English declarations in foreign reports, the preparer
also keeps the company's definition of operating working capital as inventories
plus trade receivables minus trade payables and customer advances.
It requires an explicit non-IFRS definition, a unique table with dates and
currency, exact sums and the match of every current component with the
printed balance sheet. The compiler rechecks the evidence on the original documents;
different formats or discrepancies remain visible gaps. The verified definition
stays in the AI context even when the narrative text is reduced.
This company measure does not approve the DCF perimeter: the analyst must still
justify the treatment of the other items and the projections. The annualized quarterly revenue
present in the table does not become TTM revenue or forecasts.

The USD beta statistical reference also supports EUR listings:
it keeps the stock's adjusted prices, the S&P 500 index and the provider's daily
`EURUSD=X` exchange rate. It converts the price with the exchange rate of the same date and
computes returns over common intervals, without carrying missing values
forward. The archive keeps the normalized original observations and declares
gaps, excluded dates and the last date used. An empty FX bar at the
excluded final date stays archived and does not enter the calculation. The price index,
different closing times and adjustment conventions limit the comparison:
this regression does not choose the forward-looking beta, the capital structure or
the WACC. Other currencies are not converted implicitly.

When the FCFF dossier contains normalized printed financial statements, the AI contract
also indicates their JSON paths for citing revenue. The TTM reconciliation
uses annual plus current year-to-date minus comparable prior year-to-date;
periods and units remain verified by the compiler. Printed facts are not
mixed with XBRL concepts in the same sum. These instructions apply
only to the dossier concerned, without changing bank requests.

Excerpts can declare `layout_projection: collapse_blank_lines_v1`:
the projection compacts only sequences of blank lines, leaving lines with
content, the originals and JSON documents intact. Offsets and hashes of the
excerpts always refer to the original source; the report distinguishes
excluded text, removed spacing and hash of the text sent. Citations must
appear both in the individual original fragment and in its visible version:
a sentence reassembled across removed spacing does not become a valid
literal citation. The option does not certify the completeness of the selected notes
and does not change the model's limits or the budget checks.

The configured preparer tries `ifrs_note_sections_v1` when a new FCFF context
exceeds the conservative limit. The rule recognizes an English hierarchy of
IFRS notes: it does not use preset tickers, dates or offsets. It reduces the annual report only when
the complete interim notes of the same issuer remain available together with a
management report with a later accounting period verified in the heading;
the event date does not replace that period. Ambiguous or unknown layouts
stay whole with a declared reason, and may still exceed the limit.
This rule does not certify economic coverage and does not apply to banks.

Exact reuse of an already recorded response, full or selected, comes before
the new selection and the reading of prices. Explicit manifests and resume snapshots keep
their selection. Each check considers the plan actually
accumulated: values and rationales of previous responses are not cut
to make the request fit. If the size remains excessive, preparation
stops before reserving money; it remains necessary to acquire or select other
evidence. Model limits, maximum reservation and authorization remain unchanged.

The queue applies the same context preparation, checking authorization
and ownership of the job both before selection and before the AI request.
It immediately uses the copy of the sources saved in the checkpoint: this way a resume does not
change the order of the lists in the prompt and can reuse the phases already paid for.
An interruption during selection keeps that checkpoint; the resume remains
subject to cost reconciliation and the validity of the authorization.
