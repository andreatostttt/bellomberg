"""MOD-TI (06/10): contratto modelli per i budget gate FINTI dei test Trade Idea.

Dentro una run Trade Idea il modello si legge solo dal contratto accettato
(gate.catalog_snapshot): un gate finto senza contratto e' rifiutato, mai completato
col .env. Questo helper da' ai gate finti il contratto coi default di oggi.
"""


def contratto_default(models=None):
    from bellomberg.agents.trade_idea import MODEL_IDS
    chosen = dict(MODEL_IDS if models is None else models)
    return {"models": {role: {"id": slug} for role, slug in chosen.items()}}
