"""Server-acquired and sealed source catalogs for deterministic research actions."""
from hashlib import sha256
import json
from pathlib import Path


def acquire_sources(model, as_of, directory):
    from .preparation_sources import collect_preparation_evidence
    return collect_preparation_evidence(model['ticker'], as_of=as_of, archive_root=directory,
        financial_currency=model.get('financial_currency'),
        method_id=(model.get('valuation_decision') or {}).get('method_id'))


def seal_catalog(report, directory, *, as_of, generation_id):
    from .input_preparation import _catalog, _day
    if not isinstance(report, dict):
        raise ValueError('Primary source acquisition did not return a catalog')
    documents = report.get('documents') or []
    catalog, issues, _ = _catalog(documents, _day(as_of))
    if issues or not catalog:
        raise ValueError('Primary document acquisition incomplete: '+ '; '.join(row['reason'] for row in issues))
    value = {'documents':list(catalog.values()), 'acquisition_receipt':{
        'generation_id':generation_id, 'as_of':as_of, 'origin':'server_primary_source_collector'}}
    blob = json.dumps(value,sort_keys=True,ensure_ascii=False,allow_nan=False).encode('utf-8')
    directory=Path(directory); directory.mkdir(parents=True,exist_ok=True)
    path=directory/'acquired-source-catalog.json'; path.write_bytes(blob)
    return {'status':'ready','source_catalog_path':str(path),'source_catalog_sha256':sha256(blob).hexdigest(),
        'acquired_as_of':as_of,'source_generation':generation_id,
        'documents':[{key:doc.get(key) for key in ('id','url','sha256','published_at','available_at')}
            for doc in catalog.values()], 'paid_request':False}


def read_catalog(event, root, *, generation_id):
    if event['kind']!='acquire_sources' or event['data'].get('status')!='ready':
        raise ValueError('A completed server source acquisition is required')
    if event['generation_id']!=generation_id:
        raise ValueError('Source catalog is bound to a different model generation')
    path=Path(event['data']['source_catalog_path']).resolve()
    if not path.is_relative_to(Path(root).resolve()):
        raise ValueError('Source catalog is outside the private research archive')
    blob=path.read_bytes()
    if sha256(blob).hexdigest()!=event['data']['source_catalog_sha256']:
        raise ValueError('Acquired source catalog changed after its immutable receipt')
    return json.loads(blob)


def prove_earnings_observation(report, expected, submitted, *, as_of):
    """Compatibility entry point; common output/primary measure proof is mandatory."""
    from .trade_idea_earnings_facts import prove_earnings_fact
    return prove_earnings_fact(report,expected,submitted,as_of=as_of)
