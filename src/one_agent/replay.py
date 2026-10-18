"""Delivery-ordered, accelerated replay with an explicit virtual dependency clock."""
import json
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from .common import IST, day, iso, jsonl, ts, write_json, write_jsonl
from .engine import Engine
from .measure import reports
from .pack import Pack
from .store import Store


def percentile(values, p):
    values = sorted(values)
    return values[min(len(values)-1,int((len(values)-1)*p))] if values else 0


def run(pack_path, out_path, seed='oua-practice', max_model_calls=8, max_tool_calls=20, channel=None):
    out = Path(out_path)
    if out.exists() and any(out.iterdir()):
        raise ValueError(f'Output directory is not empty: {out}. Use a new --out directory to preserve prior evidence.')
    out.mkdir(parents=True,exist_ok=True)
    pack = Pack(pack_path)
    store = Store(out/'state.sqlite3')
    history = list(jsonl(pack.root/'data/events.jsonl'))
    first_receipt = min(ts(e['received_at']) for e in history)
    pack.history_start = datetime.combine(day(first_receipt),datetime.min.time(),IST)
    engine = Engine(pack,store,channel=channel,max_model_calls=max_model_calls,max_tool_calls=max_tool_calls)
    timeline=[]
    for e in history:
        timeline.append((ts(e['received_at']),1,'event',e))
    for e in jsonl(pack.root/'replay/live_events.jsonl'):
        timeline.append((ts(e.get('deliver_at',e['received_at'])),2,'event',e))
    triggers = list(jsonl(pack.root/'replay/triggers.jsonl'))
    for e in triggers:
        timeline.append((ts(e.get('deliver_at',e['received_at'])),3,'trigger',e))
    for c in pack.controls:
        timeline.append((ts(c['at']),0,'control',c))
    timeline.sort(key=lambda x:(x[0],x[1]))
    # Controls and observed events keep flowing while the decision worker waits.
    # They are applied before a queued decision starts; pending triggers never
    # become evidence merely because they happen to be in the input file.
    ingested_early=set()
    latencies=[]; service=[]; duplicates=0; injected=0; total_started=time.perf_counter()
    worker_available=None
    for index,(at,_,kind,value) in enumerate(timeline):
        if index in ingested_early:
            continue
        if kind=='control':
            engine.control(value)
        elif kind=='event':
            engine.observe(value)
            if index%1000==0:
                store.db.commit()
        else:
            store.db.commit()
            start=max(at,worker_available) if worker_available else at
            queue_ms=(start-at).total_seconds()*1000
            if start > at:
                future_index=index+1
                while future_index < len(timeline) and timeline[future_index][0] <= start:
                    _,_,pending_kind,pending=timeline[future_index]
                    if pending_kind!='trigger' and future_index not in ingested_early:
                        if pending_kind=='control':
                            engine.control(pending)
                        else:
                            engine.observe(pending)
                        ingested_early.add(future_index)
                    future_index+=1
            # Reads run against the snapshot at service start. Publication is
            # refused if a relevant live update arrives during modeled latency.
            wall_start=time.perf_counter()
            decision,duplicate=engine.decide(value,now=start,queue_delay_ms=queue_ms)
            wall_ms=(time.perf_counter()-wall_start)*1000
            if duplicate:
                duplicates+=1
                worker_available=start+timedelta(milliseconds=wall_ms)
            else:
                wait=decision['simulated_dependency_wait_ms']; injected+=wait
                worker_available=start+timedelta(milliseconds=wall_ms+wait)
                # The JSONL action is the local publishing boundary. Timestamp it
                # after measured transaction completion, not before verification.
                changed_during_wait=[]
                for pending_at,_,pending_kind,pending in timeline[index+1:]:
                    if pending_at > worker_available:
                        break
                    if pending_at > start and (pending_kind=='control' or
                       (pending_kind=='event' and pending['user_id']==value['user_id'])):
                        changed_during_wait.append(pending)
                engine.finalize_publication(decision,worker_available,wall_ms+wait+queue_ms,
                                            changed_during_wait)
                final_wall_ms=(time.perf_counter()-wall_start)*1000
                worker_available=start+timedelta(milliseconds=final_wall_ms+wait)
                latencies.append(final_wall_ms+wait+queue_ms); service.append(final_wall_ms)
    store.db.commit()
    write_jsonl(out/'decisions.jsonl',store.records('decisions'))
    write_jsonl(out/'actions.jsonl',store.published_actions())
    write_jsonl(out/'audit.jsonl',store.records('audit'))
    write_jsonl(out/'cohorts.jsonl',store.records('cohorts'))
    # The supplied simulator remains unmodified and runs locally without network.
    subprocess.run([sys.executable,str(pack.root/'simulator/simulate_outcomes.py'),
                    '--pack',str(pack.root),'--actions',str(out/'actions.jsonl'),
                    '--out',str(out/'sim'),'--seed',seed],check=True)
    # Batch simulator evidence becomes known only at final evaluation, never retroactively.
    observation_end=datetime.combine(day(min(ts(e['received_at']) for e in triggers))+timedelta(days=15),datetime.min.time(),IST)
    for event in jsonl(out/'sim/outcome_events.jsonl'):
        store.ingest(event,observed_at=observation_end)
    store.set_time(observation_end)
    sim_report=json.loads((out/'sim/simulator_report.json').read_text())
    latency={'unique_decisions':len(latencies),'duplicate_deliveries':duplicates,
             'p50_ms':percentile(latencies,.5),'p95_ms':percentile(latencies,.95),'p99_ms':percentile(latencies,.99),
             'max_ms':max(latencies,default=0),'over_60s':sum(x>60000 for x in latencies),
             'over_5m':sum(x>300000 for x in latencies),'measured_service_p95_ms':percentile(service,.95),
             'measured_service_max_ms':max(service,default=0),'simulated_dependency_wait_total_ms':injected,
             'replay_wall_seconds_before_reports':time.perf_counter()-total_started,
             'clock_contract':'Single virtual decision worker; service measured including SQLite commit. Dependency delays modeled, not slept. Queue included; no production throughput claim.'}
    impact=reports(pack,store,out,observation_end,sim_report,latency)
    write_json(out/'run_manifest.json',{'seed':seed,'pack':str(pack.root.resolve()),'python':sys.version,
                                      'history_start':iso(pack.history_start),'observation_end':iso(observation_end),
                                      'max_model_calls':max_model_calls,'max_tool_calls':max_tool_calls,'channel':channel or 'auto'})
    store.close()
    return {'output':str(out),'decisions':len(latencies),'actions':sim_report['counts'].get('actions_total',0),
            'policy_problems':sim_report['policy_problem_count'],'primary_itt':impact['replay']['primary_completion_72h']}
