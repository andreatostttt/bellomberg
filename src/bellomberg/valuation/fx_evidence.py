"""Exact-day ECB reference FX with explicit orientation and reproducible arithmetic.

Reference rates are not transaction prices. Missing days are never carried.
The existing immutable downloader and source-availability rules are reused.
"""
import csv
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from fractions import Fraction
from hashlib import sha256
from io import StringIO
import json
from math import isfinite
from pathlib import Path
import re

from .document_evidence import source_dates

NORMALIZER='ecb_reference_fx_v1'
PREFIX='fx-ecb-'
LIMITATION='ECB daily reference rate, not a transaction or equity closing-time rate. Exact date only; no missing-day carry or inferred currency.'
CROSS_NORMALIZER='ecb_reference_cross_fx_v1'
CROSS_PREFIX='fx-ecb-cross-'
CROSS_LIMITATION=('Computed cross from two same-day ECB daily reference observations, not an observed pair quotation, '
                  'transaction price or equity closing-time rate. No missing-day carry, invented primary quote or subunit conversion.')


def reference_url(financial, quote, on):
    if (any(not isinstance(c,str) or not re.fullmatch('[A-Z]{3}',c) for c in (financial,quote))
            or financial==quote or 'EUR' not in (financial,quote)):
        raise ValueError('ECB pair requires explicit distinct ISO currencies, one EUR; no subunit or cross-rate default')
    if date.fromisoformat(on).isoformat()!=on:
        raise ValueError('exact ISO FX date required')
    foreign=quote if financial=='EUR' else financial
    return ('https://data-api.ecb.europa.eu/service/data/EXR/D.'+foreign+'.EUR.SP00.A'
            '?startPeriod='+on+'&endPeriod='+on+'&format=csvdata')


def normalize_fx(source, *, financial_currency, quote_currency, on, as_of):
    url=reference_url(financial_currency,quote_currency,on)
    day,cutoff=date.fromisoformat(on),date.fromisoformat(as_of)
    raw=source['text'].encode('utf-8'); digest=sha256(raw).hexdigest()
    if (source['url']!=url or source['id']!=digest or source['document_sha256']!=digest
            or source['sha256']!=digest or day>cutoff or source.get('published_at') is not None
            or source.get('availability_basis')!='observed_download'):
        raise ValueError('FX raw identity, hash, date, URL or availability differs')
    dates=source_dates(source,cutoff)
    if date.fromisoformat(dates['available_at'])<day:
        raise ValueError('FX receipt precedes the observation date')
    try:
        reader=csv.DictReader(StringIO(source['text'].lstrip('\ufeff')),strict=True)
        headers=reader.fieldnames
        required=('KEY','FREQ','CURRENCY','CURRENCY_DENOM','EXR_TYPE','EXR_SUFFIX',
                  'TIME_PERIOD','OBS_VALUE','OBS_STATUS','UNIT','UNIT_MULT')
        if not headers or len(set(headers))!=len(headers) or not set(required)<=set(headers):
            raise ValueError('unique ECB series/value/date/unit headers required')
        rows=list(reader)
    except csv.Error as exc:
        raise ValueError('invalid ECB CSV structure') from exc
    if len(rows)!=1 or None in rows[0] or any(rows[0].get(k) is None for k in required):
        raise ValueError('one complete exact-day ECB observation required')
    row=rows[0]; foreign=quote_currency if financial_currency=='EUR' else financial_currency
    expected={'KEY':'EXR.D.'+foreign+'.EUR.SP00.A','FREQ':'D','CURRENCY':foreign,
        'CURRENCY_DENOM':'EUR','EXR_TYPE':'SP00','EXR_SUFFIX':'A','TIME_PERIOD':on,
        'OBS_STATUS':'A','UNIT':foreign,'UNIT_MULT':'0'}
    if any(row[k]!=v for k,v in expected.items()):
        raise ValueError('ECB series, pair, exact day, normal observation status or unscaled units differ')
    value_text=row['OBS_VALUE']
    if len(value_text)>40 or not re.fullmatch(r'(?:0|[1-9][0-9]*)(?:\.[0-9]+)?',value_text):
        raise ValueError('finite positive decimal reference rate required')
    try:
        exact=Fraction(Decimal(value_text))
        if exact<=0: raise ValueError('reference rate must be positive')
        result=exact if financial_currency=='EUR' else 1/exact
        value=float(result)
    except (InvalidOperation,OverflowError) as exc:
        raise ValueError('reference conversion is not representable') from exc
    if not isfinite(value) or value<=0:
        raise ValueError('reference conversion must remain finite and positive')
    text=json.dumps({'dataset':'ECB EXR daily reference','observation':row,
        'source_document_id':source['id'],'source_line':2,
        'calculation':{'operation':'identity' if financial_currency=='EUR' else 'reciprocal',
            'exact_numerator':str(result.numerator),'exact_denominator':str(result.denominator)},
        'facts':[{'value':value,'unit':quote_currency+' per '+financial_currency,'end':on}],
        'limitation':LIMITATION},ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)
    return {'id':PREFIX+digest+'-'+financial_currency+'-'+quote_currency,'url':url,**dates,
        'text':text,'sha256':sha256(text.encode()).hexdigest(),'document_sha256':digest,
        'origin':NORMALIZER,'metadata':{'normalizer':NORMALIZER,'source_document_id':source['id'],
            'financial_currency':financial_currency,'quote_currency':quote_currency,'report_date':on,'as_of':as_of},
        'extraction_coverage':{'status':'exact_day_reference','limitation':LIMITATION}}


def collect_fx_evidence(*,financial_currency,quote_currency,on,as_of,archive_root,download=None,now=None):
    result={'status':'incomplete','documents':[],'issues':[],'limitation':LIMITATION}
    try:
        if (isinstance(financial_currency,str) and re.fullmatch('[A-Z]{3}',financial_currency)
                and financial_currency==quote_currency):
            result['status']='identity'
            return result
        url=reference_url(financial_currency,quote_currency,on)
        if date.fromisoformat(on)>date.fromisoformat(as_of):
            raise ValueError('FX observation after cutoff')
        from bellomberg.market_data.lettore_trimestrali import scarica_documento
        root=Path(archive_root).resolve()
        fetched=(download or scarica_documento)(url,str(root),host_consentiti=['data-api.ecb.europa.eu'],public_only=True)
        if fetched.get('stato')!='ok' or fetched.get('url_finale')!=url:
            raise ValueError('ECB reference unavailable or redirected: '+str(fetched.get('motivo')))
        path=Path(fetched['path']).resolve()
        if not path.is_relative_to(root): raise ValueError('FX source outside verified archive')
        raw=path.read_bytes();digest=sha256(raw).hexdigest()
        if digest!=fetched['sha256']: raise ValueError('FX byte hash differs from download receipt')
        stamp=now if now is not None else datetime.now(timezone.utc)
        source={'id':digest,'url':url,'text':raw.decode('utf-8'),'sha256':digest,'document_sha256':digest,
            'published_at':None,'availability_basis':'observed_download',
            'retrieval':{'url':url,'document_sha256':digest,'retrieved_at':stamp.isoformat()}}
        document=normalize_fx(source,financial_currency=financial_currency,quote_currency=quote_currency,on=on,as_of=as_of)
        result.update(status='ready',documents=[source,document])
    except Exception as exc:
        result['issues'].append({'source':'ECB reference FX','reason':type(exc).__name__+': '+str(exc)})
    return result


def _cross_pair(financial_currency, quote_currency):
    currencies = (financial_currency, quote_currency)
    if (any(not isinstance(c, str) or not re.fullmatch('[A-Z]{3}', c) for c in currencies)
            or financial_currency == quote_currency or 'EUR' in currencies or 'GBX' in currencies):
        raise ValueError('cross requires two explicit distinct non-EUR ISO currency units; no price subunits')


def normalize_cross_fx(financial_source, quote_source, *, financial_currency, quote_currency, on, as_of):
    """Prove Q/F = (Q/EUR)/(F/EUR) from two independently verified raw legs.

    Each raw URL, exact day, unit, status, SHA-256 and acquisition receipt is
    checked by the existing single-leg normalizer. The derived document anchors
    its availability to the later actual primary receipt; it never manufactures
    a raw cross-rate URL, publication timestamp or observation.
    """
    _cross_pair(financial_currency, quote_currency)
    financial = normalize_fx(financial_source, financial_currency=financial_currency,
        quote_currency='EUR', on=on, as_of=as_of)
    quoted = normalize_fx(quote_source, financial_currency=quote_currency,
        quote_currency='EUR', on=on, as_of=as_of)
    if financial_source['id'] == quote_source['id']:
        raise ValueError('two distinct primary currency observations required')
    first, second = json.loads(financial['text']), json.loads(quoted['text'])
    exact = Fraction(Decimal(second['observation']['OBS_VALUE'])) / Fraction(Decimal(first['observation']['OBS_VALUE']))
    value = float(exact)
    if not isfinite(value) or value <= 0:
        raise ValueError('cross conversion is not finite and positive')
    # Both legs have already passed source_dates against the same as_of.
    # Retain the original receipt, including its actual URL and byte hash.
    anchor = max((financial_source, quote_source), key=lambda doc: (
        source_dates(doc, date.fromisoformat(as_of))['available_at'],
        datetime.fromisoformat(doc['retrieval']['retrieved_at']).astimezone(timezone.utc)))
    dates = source_dates(anchor, date.fromisoformat(as_of))
    payload = {'dataset': 'ECB EXR daily reference computed cross',
        'source_document_ids': {'financial': financial_source['id'], 'quote': quote_source['id']},
        'observations': {'financial': first['observation'], 'quote': second['observation']},
        'source_lines': {'financial': 2, 'quote': 2},
        'calculation': {'operation': 'divide_quote_eur_by_financial_eur',
            'numerator_source_document_id': quote_source['id'], 'denominator_source_document_id': financial_source['id'],
            'exact_numerator': str(exact.numerator), 'exact_denominator': str(exact.denominator)},
        'facts': [{'value': value, 'unit': quote_currency+' per '+financial_currency, 'end': on}],
        'limitation': CROSS_LIMITATION}
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    digest = sha256(text.encode()).hexdigest()
    return {'id': CROSS_PREFIX+digest+'-'+financial_currency+'-'+quote_currency,
        'url': anchor['url'], **dates, 'text': text, 'sha256': digest,
        'document_sha256': anchor['document_sha256'], 'origin': CROSS_NORMALIZER,
        'metadata': {'normalizer': CROSS_NORMALIZER, 'source_document_id': anchor['id'],
            'financial_document_id': financial_source['id'], 'quote_document_id': quote_source['id'],
            'financial_currency': financial_currency, 'quote_currency': quote_currency, 'report_date': on, 'as_of': as_of},
        'extraction_coverage': {'status': 'exact_day_two_primary_reference_cross', 'limitation': CROSS_LIMITATION}}


def collect_cross_fx_evidence(*, financial_currency, quote_currency, on, as_of, archive_root, download=None, now=None):
    """Acquire two declared official EUR legs, then derive their dated cross.

    This explicit entry point preserves the existing single-leg collector's
    contract. A failed leg leaves the cross incomplete with no usable document.
    """
    result = {'status': 'incomplete', 'documents': [], 'issues': [], 'limitation': CROSS_LIMITATION}
    try:
        _cross_pair(financial_currency, quote_currency)
        legs = []
        for currency in (financial_currency, quote_currency):
            leg = collect_fx_evidence(financial_currency=currency, quote_currency='EUR', on=on, as_of=as_of,
                archive_root=archive_root, download=download, now=now)
            if leg['status'] != 'ready' or len(leg['documents']) != 2:
                raise ValueError(currency+' primary leg unavailable: '+repr(leg['issues']))
            legs.append(leg['documents'][0])
        normalized = normalize_cross_fx(*legs, financial_currency=financial_currency,
            quote_currency=quote_currency, on=on, as_of=as_of)
        result.update(status='ready', documents=[*legs, normalized])
    except Exception as exc:
        result['issues'].append({'source': 'ECB reference cross FX', 'reason': type(exc).__name__+': '+str(exc)})
    return result
