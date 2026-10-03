# Explicit diluted-share estimates

For SEC operating companies whose point-in-time diluted denominator is not
reported, preparation can explicitly choose `shares.kind=analyst_estimate` with
`dilution_estimate.method=reported_dilution_ratio_proxy`. This is optional analyst
judgment, never an automatic fallback or a historical diluted-share observation.
The compiler verifies the opening common shares outstanding and the shortest
comparable same-filing basic/diluted EPS share period, their units, SEC issuer,
accession and dates, then checks `outstanding * weighted_diluted / weighted_basic`.
The estimate must explain why that period's award mix is applicable and retain
the risks from averaging, source rounding, vesting and excluded awards. The
historical checkpoint separates the verified operands from the estimated
denominator, and Excel labels the latter as an estimate/proxy. The same claims
must not also be deducted in the equity bridge; future SBC remains in operating
costs. An unsupported proxy remains a missing input.
