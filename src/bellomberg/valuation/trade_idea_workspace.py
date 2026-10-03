"""Version-bound research tools. Deterministic operations never imply AI consent."""
from copy import deepcopy
from datetime import date, datetime, timezone
from hashlib import sha256
import json
import math
from pathlib import Path
import re
from uuid import uuid4

from bellomberg.storage.trade_idea_workspace import WorkspaceEvents, encode


def _text(value, label, maximum=6000):
    if not isinstance(value,str) or not value.strip() or len(value)>maximum:
        raise ValueError(label+" must be nonempty text within the supported length")
    return value.strip()


def _number(value):
    if isinstance(value,bool) or not isinstance(value,(float,int)) or not math.isfinite(value):
        raise ValueError("finite numeric input required")
    return value


def _date(value):
    return date.fromisoformat(value)


def _observed_url(value):
    if not isinstance(value,str) or not re.fullmatch(r"https?://[^\s<>]+",value):
        raise ValueError("public source URL required")
    return value


class ResearchWorkspace:
    """Every operation receives an explicit DB store and private artifact root."""
    def __init__(self, store, *, artifact_root, model_roots=None, clock=None, price_fetcher=None, source_fetcher=None):
        self.store = store
        self.events = WorkspaceEvents(store)
        self.root = Path(artifact_root).resolve()
        self.model_roots = [Path(p).resolve() for p in (model_roots or [self.root])]
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.price_fetcher = price_fetcher or self._public_price
        from .trade_idea_research_sources import acquire_sources
        self.source_fetcher = source_fetcher or acquire_sources

    def _models(self, detail):
        progress = detail.get("progress") or {}
        values = progress.get("valuation_generations") or progress.get("valuation_results") or {}
        values = list(values.values()) if isinstance(values,dict) else values
        ticker = detail["run"]["ticker"]
        originals = [p for p in values if isinstance(p,dict) and p.get("ticker")==ticker]
        variants = [row["data"]["model"] for row in self.events.list(detail["run"]["id"])
            if row["kind"] in ("simulate","refresh","earnings_review") and isinstance(row["data"].get("model"),dict)]
        return originals + variants

    def _model(self, detail, generation_id=None):
        models = self._models(detail)
        references = (detail.get("result") or {}).get("valuation_refs") or []
        chosen = generation_id or (references[-1].get("generation_id") if references else None)
        candidates = [p for p in models if p.get("generation_id")==chosen] if chosen else models[-1:]
        if len(candidates)!=1:
            raise ValueError("exact selected committee model generation unavailable")
        payload = candidates[0]
        from bellomberg.reporting.valuation_delivery import build_manifest
        committed_variant = any(row["data"].get("model",{}).get("generation_id")==payload.get("generation_id")
            for row in self.events.list(detail["run"]["id"]))
        if not committed_variant:
            manifest = build_manifest({detail["run"]["ticker"]:payload},roots=self.model_roots,attempts=())
            if not any(row.get("status")=="ready" for row in manifest["valuations"]):
                raise ValueError("selected model workbook unavailable: "+"; ".join(r.get("reason","") for r in manifest["valuations"]))
        else:
            path = Path(payload.get("path") or "").resolve()
            if not any(path.is_relative_to(root) for root in self.model_roots):
                raise ValueError("variant workbook outside model roots")
            if sha256(path.read_bytes()).hexdigest()!=payload.get("workbook_sha256"):
                raise ValueError("variant workbook changed after its immutable event")
            sealed = payload.get("_workspace_sidecar_sha256")
            if not sealed or sha256(path.with_suffix(".payload.json").read_bytes()).hexdigest()!=sealed:
                raise ValueError("variant sidecar changed after its immutable event")
        sidecar = json.loads(Path(payload["path"]).with_suffix(".payload.json").read_text(encoding="utf-8"))
        for key in ("generation_id","snapshot_id","valuation_date","currency","price","fair_value_bear","fair_value_base",
                    "fair_value_bull","acquisition_snapshot","method","valuation_decision","calculation_details","exposure_analysis"):
            if sidecar.get(key)!=payload.get(key):
                raise ValueError("selected model differs from its workbook: "+key)
        from .trade_idea_model import candidate_model_usability, model_exhibits
        usability = candidate_model_usability(payload)
        if not usability["usable"]:
            raise ValueError("selected model is unusable: "+"; ".join(usability["reasons"]))
        packet = model_exhibits(payload)
        if packet.get("status")!="complete":
            raise ValueError("selected workbook observations differ: "+"; ".join(packet.get("reasons",[])))
        return deepcopy(payload)

    @staticmethod
    def _editable(model):
        if model.get("method")=="exposure_analysis":
            return []  # Observed holdings and prospectus terms are not PM assumptions.
        from .preparation_methods import method_schema
        bundle = model.get("acquisition_snapshot") or {}
        method = (model.get("valuation_decision") or {}).get("method_id")
        schema,_ = method_schema(method,(model.get("preparation") or {}).get("proposal",{}).get("plan"))
        result = []
        for row in bundle.get("case",{}).get("records",[]):
            definition = schema.get(row.get("driver"))
            if (definition and definition[3] in ("future","terminal") and definition[4] in ("number","path")
                    and row.get("kind")=="analyst_estimate" and row.get("scenario")!="model"
                    and row.get("provider")=="method_inputs"):
                result.append({key:deepcopy(row.get(key)) for key in
                               ("driver","scenario","value","unit","period","rationale","source_id")})
        return result

    def view(self, run_id, generation_id=None):
        detail = self.store.get_run(run_id)
        try:
            model = self._model(detail,generation_id)
            from .trade_idea_model import model_exhibits, candidate_model_usability
            exhibits = model_exhibits(model)
            usability = candidate_model_usability(model)
            current = {"status":"ready","generation_id":model["generation_id"],"snapshot_id":model["snapshot_id"],
                "method":(model.get("valuation_decision") or {}).get("method_id"),
                "currency":model.get("currency"),"price":model.get("price"),"valuation_date":model.get("valuation_date"),
                "values":{s:model.get("fair_value_"+s) for s in ("bear","base","bull")},
                "editable":self._editable(model),"exhibits":exhibits,
                "kind":usability["kind"],"intrinsic_value_applicable":usability["kind"]!="exposure"}
        except (ValueError,OSError,KeyError) as exc:
            current = {"status":"unavailable","reason":str(exc)}
        from .trade_idea_model import candidate_model_usability
        origins = {row["data"]["model"]["generation_id"]:row["kind"] for row in self.events.list(run_id)
            if isinstance(row["data"].get("model"),dict) and row["data"]["model"].get("generation_id")}
        return {"run_id":run_id,"ticker":detail["run"]["ticker"],"model":current,
            "history":self.events.list(run_id),"pending_actions":self.events.pending(run_id),
            "objections":self.events.pm_history(detail["run"]["ticker"]),
            "versions":[{"generation_id":m.get("generation_id"),"valuation_date":m.get("valuation_date"),
                "label":("refreshed" if origins.get(m.get("generation_id"))=="refresh" else "personal")
                    if m.get("generation_id") in origins else "committee",
                "usable":candidate_model_usability(m)["usable"]} for m in self._models(detail)],
            "cost":self.costs(run_id),"paid_features_enabled":False,
            "conclusions_status":"review_required" if detail["run"].get("phase")=="review_required" else "committee_snapshot"}

    def act(self, run_id, kind, data, *, request_id, generation_id):
        if not isinstance(data,dict):
            raise ValueError("research action must be an object")
        handlers = {"question":self._question,"simulate":self._simulate,"objection":self._objection,
            "objection_reply":self._objection_reply,"monitor":self._monitor,"monitor_check":self._monitor_check,
            "earnings_prepare":self._earnings_prepare,"earnings_review":self._earnings_review,
            "error_review":self._error_review,"error_attribution":self._error_attribution,"compare_runs":self._compare_runs,
            "compare_ideas":self._compare_ideas,"refresh":self._refresh,"export":self._export,
            "acquire_sources":self._acquire_sources}
        if kind not in handlers:
            raise ValueError("unsupported research action")
        detail = self.store.get_run(run_id)
        model = self._model(detail,generation_id)
        return self.events.apply(run_id,kind,data,
            lambda identity: handlers[kind](detail,model,data,self.root/run_id/identity),
            request_id=request_id,generation_id=model["generation_id"])

    def recover(self, run_id, request_id):
        pending=next((row for row in self.events.pending(run_id) if row['request_id']==request_id),None)
        if pending is not None:
            self._model(self.store.get_run(run_id),pending['generation_id'])
        return self.events.recover(run_id,request_id)

    def _question(self, detail, model, data, directory):
        question = _text(data.get("question"),"question",3000)
        from .trade_idea_model import model_exhibits
        exhibits = model_exhibits(model)
        terms = set(re.findall(r"[\w]+",question.lower())) - {"quale","quali","come","cosa","della","delle","the","what","which","sono","del","per","con"}
        citations,excerpts = [],[]
        if model.get("method")=="exposure_analysis":
            if terms.intersection({"valore","fair","valuation","fv"}):
                excerpts.append("Valore intrinseco non applicabile: il modello documenta esposizioni osservate, costi e condizioni dello strumento.")
                citations.append({"generation_id":model["generation_id"],"source":"documented_exposure/1",
                    "as_of":model.get("valuation_date")})
            if terms.intersection({"holdings","exposure","esposizione","esposizioni","pesi","weight","leverage","leva","concentrazione"}):
                for exhibit in exhibits.get("exhibits",[]):
                    for row,refs in zip(exhibit["rows"],exhibit["cell_refs"]):
                        excerpts.append(f"{row[0]}: {row[1]} ({exhibit['unit']}); {exhibit['period']}")
                        citations.append({"generation_id":model["generation_id"],"source":exhibit["source"],
                            "cell":refs[1],"as_of":exhibit["period"]})
        for scenario in ("bear","base","bull"):
            if scenario in terms and terms.intersection({"valore","value","valuation","fair","prezzo","price","base","bear","bull"}):
                value = model.get("fair_value_"+scenario)
                if value is not None:
                    cell = self._value_cell(model,scenario)
                    excerpts.append(f"{scenario}: {value} {model.get('currency')} / azione; {model.get('valuation_date')}")
                    citations.append({"generation_id":model["generation_id"],"snapshot_id":model["snapshot_id"],
                        "sheet":cell[0],"cell":cell[1],"source":"common valuation engine", "as_of":model.get("valuation_date")})
        research = detail.get("result") or {}
        original_refs = research.get("valuation_refs") or []
        research_generation = original_refs[-1].get("generation_id") if original_refs else None
        for evidence in research.get("evidence") or []:
            text = evidence.get("summary","")
            if terms.intersection(re.findall(r"[\w]+",text.lower())):
                excerpts.append(text)
                citations.append({"generation_id":research_generation,"evidence_id":evidence["id"],
                    "source":evidence.get("source"),"url":evidence.get("url"),"as_of":evidence.get("as_of")})
        evidence_by_id = {row["id"]:row for row in research.get("evidence") or []}
        for section in research.get("dossier") or []:
            cited = [evidence_by_id[key] for key in section.get("evidence_ids",[]) if key in evidence_by_id]
            for paragraph in section.get("paragraphs",[]):
                if cited and terms.intersection(re.findall(r"[\w]+",paragraph.lower())):
                    excerpts.append(paragraph)
                    citations.append({"generation_id":research_generation,"section":section.get("title"),
                        "evidence_ids":[row["id"] for row in cited],"source":"archived committee dossier",
                        "as_of":[row.get("as_of") for row in cited]})
        for row in self._editable(model):
            if row["driver"].lower() in question.lower():
                excerpts.append(f"{row['scenario']} {row['driver']}: {row['value']} {row['unit']}; {row['rationale']}")
                citations.append({"generation_id":model["generation_id"],"driver":row["driver"],
                    "scenario":row["scenario"],"source":row["source_id"],"period":row["period"],
                    "cell_refs":[ref["cell"] for ref in exhibits.get("input_bindings",[])
                        if ref.get("scenario")==row["scenario"] and ref.get("driver")==row["driver"]]})
        return {"question":question,"status":"answered" if excerpts else "insufficient_evidence",
            "answer":"\n\n".join(excerpts[:12]) if excerpts else "Nessuna prova pertinente nella ricerca e nel modello selezionati.",
            "citations":citations[:12],"mode":"evidence_extracts","model_modified":False,
            "generation_id":model["generation_id"],"paid_request":False}

    @staticmethod
    def _value_cell(model, scenario):
        """Locate an actual workbook value reference, never invent a worksheet."""
        from .trade_idea_model import model_exhibits
        exhibits = model_exhibits(model)
        if exhibits.get("status")!="complete":
            raise ValueError("common model cell references unavailable")
        scenarios = next(e for e in exhibits["exhibits"] if e["id"]=="scenarios")
        index = next(i for i,row in enumerate(scenarios["rows"]) if row[0]==scenario)
        reference = scenarios["cell_refs"][index][1]
        sheet,cell = reference.rsplit("!",1)
        return sheet.strip("'"),cell

    def _simulate(self, detail, model, data, directory):
        label = _text(data.get("label"),"variant label",120)
        rationale = _text(data.get("rationale"),"personal assumption rationale",3000)
        changes = data.get("changes")
        if not isinstance(changes,list) or not 1<=len(changes)<=50:
            raise ValueError("one or more editable input changes required")
        editable = {(r["scenario"],r["driver"]):r for r in self._editable(model)}
        bundle = model["acquisition_snapshot"]
        records = deepcopy(bundle["case"]["records"])
        by_key = {(r["scenario"],r["driver"]):r for r in records}
        delta,seen = [],set()
        for change in changes:
            if not isinstance(change,dict) or set(change)!={"scenario","driver","value"}:
                raise ValueError("editable input change requires scenario, driver and value")
            key = (change["scenario"],change["driver"])
            if key not in editable or key in seen:
                raise ValueError("input is not editable, is historical or duplicated")
            seen.add(key)
            previous = by_key[key]["value"]
            value = change["value"]
            if isinstance(previous,list):
                if not isinstance(value,list) or len(value)!=len(previous):
                    raise ValueError("full forecast path of unchanged length required")
                value = [_number(v) for v in value]
            else:
                value = _number(value)
            if value == previous:
                raise ValueError("simulation requires a material input change")
            row = by_key[key]
            row["value"] = value
            row["rationale"] = "PERSONAL SIMULATION / " + rationale + "; original source supports the baseline, not this revised assumption. Baseline: " + row["rationale"]
            row["kind"] = "analyst_estimate"
            delta.append({"scenario":key[0],"driver":key[1],"before":previous,"after":value,"unit":row["unit"]})
        candidate = self._recalculate(model,records,directory)
        usable = bool((candidate.get("valuation_usability") or {}).get("usable"))
        return {"status":"ready" if usable else "blocked","label":label,"rationale":rationale,
            "variant_kind":"personal","source_generation":model["generation_id"],"changes":delta,
            "model":candidate,"issues":candidate.get("valuation_usability"),"original_unchanged":True,
            "conclusions_status":"personal_scenario_not_committee_decision","invalidates_conclusion":usable,
            "reason":"A material personal model variant requires the previous operational proposal to be revalidated; the original committee research remains archived"}

    @staticmethod
    def _seal_variant(candidate):
        path = Path(candidate.get("path") or "").with_suffix(".payload.json")
        if path.is_file():
            candidate["_workspace_sidecar_sha256"] = sha256(path.read_bytes()).hexdigest()
        return candidate

    @staticmethod
    def _recalculate(model, records, directory):
        from .sector_analysis import prepare_sector_analysis
        from .dcf_engine import generate_valuation
        bundle = model["acquisition_snapshot"]
        sources = deepcopy(bundle["case"]["sources"])
        # A personal assumption set is a new acquisition envelope. Other facts
        # remain sourced; original bytes and all deltas stay in the run archive.
        replacement = [r for r in records if r.get("provider")=="method_inputs"]
        sources["method_inputs"] = {"status":"ok","source_id":"personal_assumptions",
            "as_of":bundle["case"]["as_of"],"records":replacement,
            "data":{"source_snapshot_id":model["snapshot_id"],"source_generation_id":model["generation_id"],
                "previous_acquisition":deepcopy(sources.get("method_inputs"))}}
        providers = {name:(lambda *_a,value=source,**_k:deepcopy(value)) for name,source in sources.items()}
        revised = prepare_sector_analysis(model["ticker"],as_of=bundle["case"]["as_of"],providers=providers,
            user_context={"assumptions":deepcopy(bundle["case"].get("assumptions",{})),
                "analysis_context":deepcopy(bundle["analysis_context"])})
        return ResearchWorkspace._seal_variant(generate_valuation(model["ticker"],prepared_bundle=revised,output_dir=str(directory)))

    def _objection(self, detail, model, data, directory):
        return {"text":_text(data.get("text"),"objection"),"driver":data.get("driver"),
                "status":"open","decision_id":(detail["run"].get("destination") or {}).get("decision_id")}

    def _objection_reply(self, detail, model, data, directory):
        prior = self.events.get(detail["run"]["id"],data.get("objection_id"))
        if prior["kind"]!="objection" or data.get("status") not in ("open","answered","resolved"):
            raise ValueError("objection reply target or status invalid")
        return {"objection_id":prior["id"],"text":_text(data.get("text"),"response"),
                "status":data["status"],"author":"PM","evidence_ids":data.get("evidence_ids",[])}

    def _monitor(self, detail, model, data, directory):
        if data.get("active") is not True:
            raise ValueError("explicit monitor activation required")
        if data.get("metric") not in ("price","fair_value_base") or data.get("operator") not in ("lt","gt"):
            raise ValueError("observable model metric and comparison required")
        return {"metric":data["metric"],"operator":data["operator"],"threshold":_number(data.get("threshold")),
            "active":True,"currency":model.get("currency"),"generation_id":model["generation_id"],
            "schedule":"manual","recurring_spend":False,"last_checked":None}

    def _monitor_check(self, detail, model, data, directory):
        prior = self.events.get(detail["run"]["id"],data.get("monitor_id"))
        if prior["kind"]!="monitor" or not prior["data"].get("active"):
            raise ValueError("active monitor required")
        rule = prior["data"]
        if prior["generation_id"]!=model["generation_id"] or rule["generation_id"]!=model["generation_id"]:
            raise ValueError("monitor is bound to a different model generation; create an explicit new condition")
        if rule["currency"]!=model.get("currency"):
            raise ValueError("monitor and observation currencies differ")
        quote = model.get("market_quote") or {}
        current_price = rule["metric"]=="price" and quote.get("status")=="ok"
        value = quote.get("price") if current_price else model.get(rule["metric"])
        observed = quote.get("observed_local_date") if current_price else model.get("valuation_date")
        stale = not observed or (self.clock().date()-_date(observed)).days>7
        triggered = None if stale or value is None else (value<rule["threshold"] if rule["operator"]=="lt" else value>rule["threshold"])
        return {"monitor_id":prior["id"],"status":"stale" if stale else "insufficient_evidence" if value is None else "triggered" if triggered else "not_triggered",
            "value":value,"observed_at":observed,"checked_at":self.clock().isoformat(),"alert":triggered,
            "source":quote.get("source_id") if current_price else "selected model at its valuation date; no live refresh implied",
            "generation_id":model["generation_id"]}

    def _earnings_prepare(self, detail, model, data, directory):
        event = _date(data.get("event_date"))
        if event <= self.clock().date():
            raise ValueError("earnings preparation must precede the event date")
        from .trade_idea_earnings_facts import model_earnings_expectations
        packet = model_earnings_expectations(model)
        expected = [entry for entry in packet['expectations'] if _date(entry['period_end']) < event]
        selected = data.get("metric_keys")
        if selected is not None:
            if not isinstance(selected,list) or not selected or not all(isinstance(x,str) for x in selected):
                raise ValueError("nonempty metric keys required")
            available = {r["driver"]+":"+r["period"] for r in expected}
            if not set(selected)<=available:
                raise ValueError("selected expectation does not precede the earnings event")
            expected = [r for r in expected if r["driver"]+":"+r["period"] in selected]
        return {"status":"ready" if expected else "insufficient_evidence","event_date":event.isoformat(),
            "frozen_at":self.clock().isoformat(),"generation_id":model["generation_id"],"expectations":expected,
            "metrics_contract":packet['contract'],"metrics_unavailable":packet['unavailable_metrics'],
            "reasons":packet['reasons'],"workbook_sha256":packet['workbook_sha256'],
            "consensus_status":"not_available","basis":"committee_model_not_market_consensus",
            "scenarios":deepcopy((detail.get("result") or {}).get("scenarios") or []),
            "decision_conditions":deepcopy((detail.get("result") or {}).get("invalidation") or [])}

    def _earnings_review(self, detail, model, data, directory):
        prior = self.events.get(detail["run"]["id"],data.get("preparation_id"))
        if prior["kind"]!="earnings_prepare":
            raise ValueError("frozen pre-earnings preparation required")
        if prior["generation_id"]!=model["generation_id"] or prior["data"]["generation_id"]!=model["generation_id"]:
            raise ValueError("earnings expectations are bound to a different model generation")
        report = None
        observations = data.get("observations")
        if not isinstance(observations,list) or not observations:
            raise ValueError("observed earnings data with source required")
        expectations = {(r["driver"],r["period"],r["entity"]):r for r in prior["data"]["expectations"]}
        differences = []
        seen = set()
        for row in observations:
            key = (row.get("driver"),row.get("period"),row.get("entity"))
            expected = expectations.get(key)
            if key in seen:
                raise ValueError("duplicate earnings observation")
            seen.add(key)
            if expected is None or any(row.get(k)!=expected.get(k) for k in ("unit","period","entity")):
                raise ValueError("earnings unit, period or entity perimeter differs from frozen expectations")
            published = _date(row.get("published_at"))
            if published>self.clock().date() or published<_date(prior["data"]["event_date"]):
                raise ValueError("observation publication date is future or precedes the earnings event")
            if published < datetime.fromisoformat(prior["data"]["frozen_at"]).date():
                raise ValueError("look-ahead: expectation frozen after publication")
            source = _observed_url(row.get("source"))
            if report is None:
                report = self._source_catalog(detail,model,data.get("source_event_id"))
            from .trade_idea_earnings_facts import prove_earnings_fact
            proof = prove_earnings_fact(report,expected,row,as_of=self.clock().date().isoformat())
            baseline = expected["value"]
            if isinstance(baseline,list):
                if not isinstance(row.get("value"),list) or len(row["value"])!=len(baseline):
                    raise ValueError("earnings observed path and period must match exactly")
                value = [_number(x) for x in row["value"]]
                delta = [a-b for a,b in zip(value,baseline)]
            else:
                value = _number(row.get("value")); delta = value-baseline
            differences.append({"driver":row["driver"],"expected":baseline,"observed":value,
                "delta":delta,"unit":row["unit"],"period":row["period"],"entity":row["entity"],
                "source":source,"published_at":published.isoformat(),"source_proof":proof})
        impact = None
        if data.get("changes"):
            impact = self._simulate(detail,model,{"label":"Post-earnings personal revision",
                "rationale":_text(data.get("rationale"),"forward assumption revision rationale"),
                "changes":data["changes"]},directory)
        result = {"status":"review_required","preparation_id":prior["id"],"differences":differences,
            "model_impact":"requires_explicit_assumption_revision","source_generation":prior["generation_id"],
            "expectations_preserved":True,"paid_request":False,"invalidates_conclusion":True,
            "observation_status":"verified_primary_source","source_event_id":data["source_event_id"],
            "reason":"Observed results require the dated thesis and operating proposal to be revalidated",
            "thesis_impact":{"status":"review_required","conditions":prior["data"].get("decision_conditions",[])}}
        if impact:
            from .trade_idea_comparison import model_comparison
            result.update(model=impact["model"],revision=impact,model_impact=model_comparison(model,impact["model"]))
        return result

    def _error_review(self, detail, model, data, directory):
        history = self.events.list(detail["run"]["id"])
        reviews = [row for row in history if row["kind"]=="earnings_review"]
        if not reviews:
            return {"status":"insufficient_history","reason":"No later comparable observations for the selected research", "findings":[]}
        attributions = [row for row in history if row["kind"]=="error_attribution"]
        findings, matched = [], 0
        for row in reviews:
            attributed = sorted({item["data"]["category"] for item in attributions
                if item["data"]["evidence_event_id"]==row["id"]})
            for item in row["data"]["differences"]:
                delta = item["delta"]
                if delta==0 or (isinstance(delta,list) and delta and all(value==0 for value in delta)):
                    matched += 1
                    continue
                findings.append({"event_id":row["id"],"generation_id":row["generation_id"],
                    "category":attributed[0] if len(attributed)==1 else
                        "multiple_attributions" if attributed else "unattributed_deviation",
                    "attributed_categories":attributed,"attribution_author":"PM" if attributed else None,
                    "evidence":item,"data_error_status":"not_established",
                    "causal_attribution_established":False})
        return {"status":"observational_review","findings":findings,"attributions":attributions,
            "matched_observations":matched,"review_generation_id":model["generation_id"],
            "categories":{category:{"status":"documented" if any(r["data"]["category"]==category for r in attributions) else "insufficient_evidence",
                "basis":"PM attributions are recorded judgments; observed differences alone do not establish their cause"}
                for category in ("data","assumption","timing","judgment")},"causal_improvement_claimed":False}

    def _error_attribution(self, detail, model, data, directory):
        if data.get("category") not in ("data","assumption","timing","judgment"):
            raise ValueError("error category must distinguish data, assumption, timing and judgment")
        evidence = self.events.get(detail["run"]["id"],data.get("evidence_event_id"))
        if evidence["kind"] not in ("earnings_review","refresh","monitor_check"):
            raise ValueError("later observed evidence required for historical error attribution")
        if evidence["data"].get("status") in ("blocked","stale","insufficient_evidence"):
            raise ValueError("insufficient observed evidence for error attribution")
        if evidence["kind"]=="monitor_check":
            # Re-reading the original model is not a later economic observation.
            refreshed = [row for row in self.events.list(detail["run"]["id"]) if row["kind"]=="refresh"
                and row["data"].get("status")=="ready"
                and row["data"].get("model",{}).get("generation_id")==evidence["generation_id"]]
            if not refreshed or evidence["data"].get("source")=="selected model at its valuation date; no live refresh implied":
                raise ValueError("later source observation required; baseline model check is not subsequent evidence")
        return {"category":data["category"],"evidence_event_id":evidence["id"],
            "evidence_generation_id":evidence["generation_id"],"review_generation_id":model["generation_id"],
            "rationale":_text(data.get("rationale"),"error attribution rationale"),"author":"PM",
            "status":"documented_attribution","causal_improvement_claimed":False}

    def export_preview(self, run_id):
        detail = self.store.get_run(run_id)
        from bellomberg.reporting.trade_idea_share import preview_shareable_package
        return preview_shareable_package(detail["run"],detail.get("result") or {},self._reviewed_models(detail),model_roots=self.model_roots)

    def _reviewed_models(self, detail):
        ids = {row["generation_id"] for row in (detail.get("result") or {}).get("valuation_refs",[])}
        return [model for model in self._models(detail) if model.get("generation_id") in ids]

    def _export(self, detail, model, data, directory):
        from bellomberg.reporting.trade_idea_share import build_private_package,build_shareable_package
        reviewed = self._reviewed_models(detail)
        if model["generation_id"] not in {item["generation_id"] for item in reviewed}:
            raise ValueError("Select the reviewed committee version to export its coherent PDF and Excel package")
        if data.get("scope")=="private":
            if not detail.get("artifacts"):
                raise ValueError("sealed private package unavailable")
            return build_private_package(detail["artifacts"],output_path=directory/"private-package.zip")
        if data.get("scope")!="shareable" or data.get("exclusions_acknowledged") is not True:
            raise ValueError("shareable export requires previewed exclusions to be acknowledged")
        return build_shareable_package(detail["run"],detail.get("result") or {},reviewed,
            output_dir=directory,model_roots=self.model_roots,language=detail["run"].get("language","it"))

    def costs(self, run_id):
        from .trade_idea_comparison import cost_breakdown
        return cost_breakdown(self.store,run_id,self.events.list(run_id))

    def _compare_runs(self, detail, model, data, directory):
        from .trade_idea_comparison import model_comparison
        other = self.store.get_run(_text(data.get("other_run_id"),"other run",80))
        target = self._model(other,data.get("other_generation_id"))
        if other["run"]["ticker"]!=detail["run"]["ticker"]:
            raise ValueError("run comparison requires the same exact ticker; use ideas comparison")
        comparison = model_comparison(model,target)
        comparison.update(status="ready",other_run_id=other["run"]["id"],
            analysis={key:{"before":(detail.get("result") or {}).get(key),"after":(other.get("result") or {}).get(key)}
                for key in ("summary","judgment","scenarios","risks","catalysts","evidence","pm_view_response","history_review")},
            pm_objections={"before":[r for r in self.events.pm_history(model["ticker"]) if r["run_id"]==detail["run"]["id"]],
                "after":[r for r in self.events.pm_history(model["ticker"]) if r["run_id"]==other["run"]["id"]]})
        return comparison

    def _compare_ideas(self, detail, model, data, directory):
        ids = data.get("run_ids")
        if not isinstance(ids,list) or not 1<=len(ids)<=5 or len(set(ids))!=len(ids):
            raise ValueError("one to five distinct ideas required")
        ids = [detail["run"]["id"],*[value for value in ids if value!=detail["run"]["id"]]]
        ideas = []
        for run_id in ids:
            item = self.store.get_run(_text(run_id,"run id",80))
            result = item.get("result") or {}
            try:
                value = self._model(item)
                quote = value.get("market_quote") or {}
                method = (value.get("valuation_decision") or {}).get("method_id")
                valuation = {"status":"ready","generation_id":value["generation_id"],"method":method,
                    "price":quote.get("price") if quote.get("status")=="ok" else value.get("price"),
                    "price_date":quote.get("observed_local_date") if quote.get("status")=="ok" else value.get("valuation_date"),
                    "currency":value.get("currency"),"valuation_date":value.get("valuation_date"),
                    "scenarios":{s:value.get("fair_value_"+s) for s in ("bear","base","bull")}}
            except (ValueError,OSError,KeyError) as exc:
                valuation = {"status":"unavailable","reason":str(exc)}
            ideas.append({"run_id":run_id,"ticker":item["run"]["ticker"],"valuation":valuation,
                **{key:deepcopy(result.get(key)) for key in ("summary","judgment","scenarios","catalysts","risks","evidence")},
                "book_compatibility":{"status":"historical_decision_only","destination":item["run"].get("destination"),
                    "reason":"Book compatibility belongs to each dated run and must be revalidated before a trade"}})
        return {"status":"ready","ideas":ideas,"numeric_ranking":None,
            "comparability":"Different securities, dates and methods are displayed separately; no mixed multiples or implied probabilities"}

    @staticmethod
    def _public_price(ticker):
        import yfinance as yf
        info = yf.Ticker(ticker).info
        retrieved = datetime.now(timezone.utc)
        fields = ("symbol","regularMarketPrice","regularMarketTime","currency","fullExchangeName",
            "exchangeTimezoneName","quoteSourceName","exchangeDataDelayedBy")
        return {"status":"ok","source_id":"https://finance.yahoo.com/quote/"+ticker+"/",
            "as_of":retrieved.date().isoformat(),"retrieved_at":retrieved.isoformat(),
            "data":{"info":{k:info.get(k) for k in fields}}}

    def _acquire_sources(self, detail, model, data, directory):
        if data:
            raise ValueError("Source acquisition uses the exact selected issuer, never browser-supplied document bodies or URLs")
        from .trade_idea_research_sources import seal_catalog
        as_of=self.clock().date().isoformat()
        report=self.source_fetcher(model,as_of,directory/"sources")
        return seal_catalog(report,directory,as_of=as_of,generation_id=model["generation_id"])

    def _source_catalog(self, detail, model, event_id):
        from .trade_idea_research_sources import read_catalog
        event=self.events.get(detail["run"]["id"],event_id)
        return read_catalog(event,self.root,generation_id=model["generation_id"])

    def _refresh(self, detail, model, data, directory):
        if data.get("kind")=="documents":
            from .trade_idea_source_refresh import refresh_model_sources
            report=self._source_catalog(detail,model,data.get("source_event_id"))
            outcome=refresh_model_sources(model,report,as_of=self.clock().date().isoformat(),output_dir=directory,apply=True)
            candidate=outcome.pop("payload",None)
            ready=outcome["status"]=="applied" and isinstance(candidate,dict)
            changed=any(row.get("before_value")!=row.get("after_value") for row in outcome.get("changes",[]))
            return {"status":"ready" if ready else "blocked","kind":"documents","source_generation":model["generation_id"],
                "source_event_id":data["source_event_id"],"source_refresh":outcome,
                **({"model":self._seal_variant(candidate)} if ready else {}),
                "invalidates_conclusion":ready or changed,"economic_rollforward":False,"paid_request":False,
                "reason":"New primary document generation requires the dated thesis and operational proposal to be revalidated"}
        if data.get("kind")!="price":
            raise ValueError("supported deterministic refresh requires a price observation; document revision requires verified source mapping")
        from .sector_analysis import prepare_sector_analysis
        from .dcf_engine import generate_valuation
        bundle = model["acquisition_snapshot"]
        sources = deepcopy(bundle["case"]["sources"])
        observation = self.price_fetcher(model["ticker"])
        if not isinstance(observation,dict) or observation.get("status")!="ok":
            raise ValueError("public price source unavailable")
        info = observation.get("data",{}).get("info",{})
        if info.get("symbol")!=model["ticker"]:
            raise ValueError("price observation belongs to a different ticker")
        if _date(observation.get("as_of"))!=self.clock().date():
            raise ValueError("price acquisition date differs from today")
        # Preserve classification evidence and method records. A new observation
        # reprices the historical value; it never rolls financials forward.
        original = sources["profile"]
        original_info = original.get("data", {}).get("info", {})
        if not info.get("fullExchangeName") or info["fullExchangeName"] != original_info.get("fullExchangeName"):
            raise ValueError("price quotation venue differs from the selected model")
        updated = deepcopy(original)
        updated.update(as_of=observation["as_of"],source_id=_observed_url(observation.get("source_id")),
            retrieved_at=observation.get("retrieved_at"))
        updated["data"]["info"].update(info)
        updated["data"]["refresh_basis"] = {"source_generation":model["generation_id"],"observation":observation}
        sources["profile"] = updated
        from .trade_idea_model import _current_quotation_evidence
        quotations = [row for row in bundle["case"]["records"]
                      if row.get("scenario") == "model" and row.get("driver") == "quotation"]
        if len(quotations) != 1:
            raise ValueError("price quotation contract unavailable or ambiguous")
        observed_bundle = {"case":{**bundle["case"], "as_of":observation["as_of"],
                                  "sources":sources, "info":info}}
        problem, _ = _current_quotation_evidence(observed_bundle,
            {"model":{"quotation":{"value":quotations[0]["value"]}}}, observation["as_of"])
        if problem:
            raise ValueError(problem)
        providers = {name:(lambda *_a,value=source,**_k:deepcopy(value)) for name,source in sources.items()}
        refreshed = prepare_sector_analysis(model["ticker"],as_of=observation["as_of"],providers=providers,
            user_context={"assumptions":deepcopy(bundle["case"].get("assumptions",{})),
                "analysis_context":deepcopy(bundle["analysis_context"])})
        candidate = self._seal_variant(generate_valuation(model["ticker"],prepared_bundle=refreshed,output_dir=str(directory)))
        quote = candidate.get("market_quote") or {}
        from .trade_idea_model import candidate_model_usability
        usability = candidate_model_usability(candidate)
        usable = usability["usable"]
        ready = usable and quote.get("status")=="ok"
        original_quote = model.get("market_quote") or {}
        before_observed = original_quote.get("status")=="ok"
        before = original_quote.get("price") if before_observed else model.get("price")
        before_date = (original_quote.get("observed_local_date") if before_observed else
                       quotations[0]["value"].get("price_as_of") or model.get("valuation_date"))
        observed = quote.get("price")
        return {"status":"ready" if ready else "blocked","kind":"price","model":candidate,
            "source_generation":model["generation_id"],"observation":observation,"quote":quote,
            "delta":{"before":before,"after":observed,"currency":model.get("currency"),
                "before_date":before_date,
                "after_date":quote.get("observed_local_date"),
                "change":observed-before if type(observed) in (int,float) and type(before) in (int,float) else None},
            "updated_components":["observed_price","price_move_since_valuation"] +
                ([] if candidate.get("intrinsic_value_applicable") is False else ["upside_to_historical_fair_value"]),
            "economic_rollforward":False,"invalidates_conclusion":True,
            "reason":"New price/source generation requires the previous conclusion and operational proposal to be revalidated",
            "issues":usability,"paid_request":False}
