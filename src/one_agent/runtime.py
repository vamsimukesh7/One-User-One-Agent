"""Bounded optional model execution and explicit decision contracts."""
import multiprocessing
import time
from .provider import ProviderResult, ReplayProvider


def _invoke(provider, trigger, candidates, at, receive, send):
    receive.close()
    try:
        send.send(provider.propose(trigger, candidates, at))
    except Exception as exc:
        send.send(ProviderResult(None, 'provider_transport_failure:' + type(exc).__name__))
    finally:
        send.close()


def bounded_propose(provider, trigger, candidates, at, timeout_ms):
    if timeout_ms <= 0:
        return ProviderResult(None, 'model_deadline_exhausted')
    # The built-in replay provider is pure local code with an explicit virtual delay.
    if isinstance(provider, ReplayProvider):
        return provider.propose(trigger, candidates, at)
    # Killable process, rather than a thread whose blocked call survives timeout.
    # Custom providers must be process-safe (picklable on Windows); transports need deadlines too.
    ctx = multiprocessing.get_context('fork' if 'fork' in multiprocessing.get_all_start_methods() else 'spawn')
    receive, send = ctx.Pipe(duplex=False)
    process = ctx.Process(target=_invoke, args=(provider,trigger,candidates,at,receive,send), daemon=True)
    started=time.perf_counter()
    try:process.start()
    except Exception as exc:
        receive.close();send.close()
        return ProviderResult(None,'provider_transport_failure:'+type(exc).__name__)
    send.close()
    try:
        if receive.poll(max(0,timeout_ms / 1000-(time.perf_counter()-started))):
            try:
                result = receive.recv()
                return result if isinstance(result, ProviderResult) else ProviderResult(None, 'malformed_provider_result')
            except EOFError:
                return ProviderResult(None, 'provider_process_failed')
        return ProviderResult(None, 'model_timeout')
    finally:
        if process.is_alive():
            process.terminate()
        process.join(timeout=.05)
        receive.close()


def validate_decision(decision):
    required = {'decision_id','viewer_id','trigger','goal','action','dialect','content',
                'confidence','confidence_reason','evidence','assumptions','checks','decided_in_ms','status'}
    if not required <= decision.keys():
        raise ValueError('Incomplete decision schema: ' + ','.join(sorted(required-decision.keys())))
    if not {'event','received_at','event_age_s','event_id'} <= decision['trigger'].keys():
        raise ValueError('Incomplete trigger schema')
    if decision['action']=='NO_ACTION' and not decision.get('no_action_reason'):
        raise ValueError('Abstention needs an explicit reason')
