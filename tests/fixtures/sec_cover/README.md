Frozen public SEC fixture for offline source admission (2 October 2026).

`uber-20260630.part-1.html` (1,200,000 bytes) followed by
`uber-20260630.part-2.html` (1,219,544 bytes) concatenates to exactly
2,419,544 bytes, SHA-256
`1b96b259139fbe986b1ca12a12e13f9e8ca8c6d393a40d9e581c574856861279`.
The original document is the Uber Technologies Form 10-Q at
https://www.sec.gov/Archives/edgar/data/1543151/000154315126000032/uber-20260630.htm.
This is an unchanged frozen public document, not synthetic financial data.
The deterministic byte split only packages the source as scanner-readable UTF-8
files below the per-file size limit. Neither part is a standalone document;
tests concatenate their bytes before ingestion and verify the original size
and hash. No tags, whitespace, metadata or source text have been edited.

`uber-catalog.json` is the existing SEC catalog tool result received during the
same run, extracted from its preserved `catalog_received` journal event. It is
a normalized tool receipt, not an original SEC submissions response body.

`uber-source-rejected-baseline.json` preserves the rejection payload reproduced
before the fix by the isolated `sec-cover-red-01` test. The transport field
`request_fingerprint` names the deterministic SHA-256 of its public `source`
object. The test recomputes that hash with the native canonical JSON settings,
then restores the historical field name `key` in memory for exact payload
comparison. Only the transport field name changes; the value and source are
unchanged, and no credential is present. Its receipt fingerprint
matches the original rejected filing receipt. Tests preserve that decision and
submit a new explicit primary-text locator against the same cached bytes.

`uber-accepted-legacy.json` is a valid offline receipt captured with the original
reader and explicit cover quotations before extraction compatibility was added.
Only its archive paths are portable relative paths. Its original text SHA-256 is
`f57dbb68cead60ebfc6e7053c8cfaee2c87ea8d0565bb915daacd9dd72fa237b`;
tests re-create and verify the complete raw document and publication receipt in
the temporary archive. Replaying preserves the receipt, text hash and quotation
offsets; altered bytes, text hashes, locators and unknown extractor versions fail.

All tests use temporary archives and simulated HTTP transport. No live SEC
request, private database or original personal archive path is accessed.
