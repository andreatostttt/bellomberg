"""Pure numeric primitives shared by TI and weekly audit; unchanged TI semantics."""
import json
import re
from decimal import Decimal, InvalidOperation

_MONTHS = ("jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|gen|feb|mar|apr|mag|giu|"
           "lug|ago|set|ott|nov|dic")

_QUANTITY_LOCAL = re.compile(
    r"(?<![\w.,])[-+−]?(?:\d{1,3}(?:[.,]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?)(?![.,]?\d)"
    r"(?:\s?(?:%|bps|pp|€|\$|EUR|USD|GBP|GBX|bn|billion|billions|million|millions|mld|mln|"
    r"miliardi|miliardo|milioni|milione|mila|migliaia|mrd|bilioni|bilione|trilioni|trilione|m|k|x))?(?![\w])",
    re.I)
_UNIT_SCALE = {"bn": 9, "billion": 9, "billions": 9, "mld": 9, "miliardi": 9, "miliardo": 9,
               "million": 6, "millions": 6, "mln": 6, "milioni": 6, "milione": 6, "m": 6,
               "mila": 3, "migliaia": 3, "k": 3, "mrd": 9,
               # Italian long scale: bilione = 10^12, trilione = 10^18.
               "bilioni": 12, "bilione": 12, "trilioni": 18, "trilione": 18}
# Tool field names that declare their own scale (revenue_eur_m = millions of euro).
_FIELD_SCALE = ((re.compile(r"(?:^|_)(?:bn|b|mld|billions?)$", re.I), 9),
                (re.compile(r"(?:^|_)(?:m|mn|mm|mln|millions?)$", re.I), 6),
                (re.compile(r"(?:^|_)(?:k|thousands?)$", re.I), 3))
# Text numbers inside receipts: English thousands groups ("1,234.5") stay one number.
_OUTPUT_TEXT_NUMBER = re.compile(
    r"(-?\d{1,3}(?:,\d{3})+(?:\.\d+)?(?![\d,])|-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)"
    r"(?:\s?(billions?|bn|mld|miliardi|millions?|mln|milioni|thousands?|mila|k)\b)?", re.I)
_TEXT_SCALE = {"billion": 9, "billions": 9, "bn": 9, "mld": 9, "miliardi": 9, "million": 6, "millions": 6,
               "mln": 6, "milioni": 6, "thousand": 3, "thousands": 3, "mila": 3, "k": 3}


def _local_number_values(token, language):
    """Every reading of a localized numeric token as (signed value, decimals).

    it (default): '.' groups thousands, ',' is the decimal mark; en: the reverse.
    The convention of the output language decides ('1.250' is 1250 in Italian); a lone
    separator that is not a thousands group is a decimal mark ('12.5'); a leading zero
    group ('0.125') is always decimal.
    """
    from decimal import Decimal
    stripped = token.strip()
    negative = stripped[:1] in ("-", "−")
    body = re.match(r"[-+−]?([\d.,]+)", stripped).group(1).rstrip(".,")
    group, decimal = (",", ".") if language == "en" else (".", ",")
    readings = set()

    def read(group_mark, decimal_mark):
        if not re.fullmatch(r"\d{1,3}(?:" + re.escape(group_mark) + r"\d{3})*(?:"
                            + re.escape(decimal_mark) + r"\d+)?|\d+(?:"
                            + re.escape(decimal_mark) + r"\d+)?", body):
            return
        text = body.replace(group_mark, "").replace(decimal_mark, ".")
        value = Decimal(text)
        readings.add((-value if negative else value, len(text.split(".")[1]) if "." in text else 0))

    if re.fullmatch(r"0[.,]\d+", body):
        read("", body[1])            # '0.125' / '0,125': always a decimal
        return readings
    read(group, decimal)
    separators = body.count(".") + body.count(",")
    if separators == 1 and not readings:
        read(decimal, group)        # a lone mark that is not a valid thousands group
    return readings


def _field_scale(key):
    for pattern, exponent in _FIELD_SCALE:
        if pattern.search(str(key or "")):
            return exponent
    return 0


def _output_numbers(text):
    """Signed numbers of a receipt as (value, scale exponent declared by its field name)."""
    from decimal import Decimal
    found = []

    def text_numbers(value, exponent):
        for raw, word in _OUTPUT_TEXT_NUMBER.findall(str(value)):
            try:
                # A scale word written after the number in the receipt text wins.
                found.append((Decimal(raw.replace(",", "")), _TEXT_SCALE.get(word.lower(), exponent)
                              if word else exponent))
            except InvalidOperation:
                continue

    def visit(node, exponent, depth):
        if depth > 12:
            return
        if isinstance(node, dict):
            for key, value in node.items():
                visit(value, _field_scale(key), depth + 1)
        elif isinstance(node, list):
            for value in node:
                visit(value, exponent, depth + 1)
        elif isinstance(node, bool) or node is None:
            return
        elif isinstance(node, (int, float)):
            number = Decimal(str(node))
            if number.is_finite():
                found.append((number, exponent))
        else:
            text_numbers(node, exponent)
    try:
        visit(json.loads(text), 0, 0)
    except (TypeError, ValueError):
        text_numbers(text or "", 0)
    return found


def _number_attested(token, language, numbers):
    """Rounded, SIGNED comparison of a prose token with receipt numbers.

    A scale word ('1,2 miliardi') is compared only at that scale, with the receipt
    value brought to units through its field name (revenue_eur_m); a bare token is
    compared with the receipt value as written; '12,5%' also matches a 0.125 ratio.
    """
    from decimal import Decimal, ROUND_HALF_UP
    unit = re.sub(r"^[-+−]?[\d.,]+\s?", "", token.strip()).lower()
    group = "," if language == "en" else "."
    if unit == "%" and group in token:
        return False                # '41.025%': a grouped percentage is ambiguous, never x100
    for value, places in _local_number_values(token, language):
        quantum = Decimal(1).scaleb(-places)
        for number, exponent in numbers:
            if unit in _UNIT_SCALE:
                candidates = [number.scaleb(exponent - _UNIT_SCALE[unit])]
            else:
                candidates = [number] + ([number.scaleb(2)] if unit == "%" else [])
            for candidate in candidates:
                try:
                    if candidate.quantize(quantum, rounding=ROUND_HALF_UP) == value:
                        return True
                except InvalidOperation:
                    continue    # beyond decimal precision: not this reading
    return False


_YEAR_UNITS_V4 = (r"\s*%|\s*(?:bps|pp|USD|EUR|GBP|GBX|million|millions|billion|billions|bn|milioni|milione|"
                  r"mln|miliardi|miliardo|mld|mila)\b|\s*€")


def _quantity_spans_v4(text):
    """/4 numeric tokens with their positions in the ORIGINAL text.

    Same exclusions as _quantities (URLs, dates, FY/CY/Q labels, years, a list number
    at the start), blanked with spaces of the same length so the spans stay valid; a
    year-shaped number followed by an Italian or English unit (2050 milioni, 1980 mln,
    2045 €) is an amount, not a year. Known limits: numbers written in words, a number
    right after a Q/FY label ("Q 470") and a list number at the start are not read.
    """
    source = str(text or "")
    blank = lambda match: " " * len(match.group())
    scrubbed = re.sub(r"https?://\S+", blank, source)
    scrubbed = re.sub(r"\b(?:19|20)\d{2}[-/]\d{1,2}(?:[-/]\d{1,2})?\b", blank, scrubbed)
    scrubbed = re.sub(r"\b\d{1,2}\s+(?:" + _MONTHS + r")[a-z]*\s+(?:19|20)\d{2}\b", blank, scrubbed, flags=re.I)
    scrubbed = re.sub(r"\b(?:FY|CY|Q)[ -]?\d{1,4}\b", blank, scrubbed, flags=re.I)

    def year_or_amount(match):
        before = scrubbed[max(0, match.start() - 8):match.start()]
        after = scrubbed[match.end():match.end() + 12]
        if (re.search(r"(?:[$€]|\b(?:USD|EUR|GBP|GBX))\s*$", before, re.I)
                or re.match(_YEAR_UNITS_V4, after, re.I)):
            return match.group()
        return " " * len(match.group())
    scrubbed = re.sub(r"\b(?:19|20)\d{2}\b", year_or_amount, scrubbed)
    scrubbed = re.sub(r"^\s*\d+(?:\.\d+)*[.)]\s+", blank, scrubbed)
    return [(match.group().strip(), match.start(), match.end()) for match in _QUANTITY_LOCAL.finditer(scrubbed)]

