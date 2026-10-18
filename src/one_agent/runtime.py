"""Bounded optional model execution and explicit decision contracts."""
import multiprocessing
from .provider import ProviderResult, ReplayProvider


def bounded_propose(provider, trigger, candidates, at, timeout_ms):
    if timeout_ms <= 0:
        return ProviderResult(None, 'model_deadline_exhausted')
    # The built-in replay provider is pure local code with an explicit virtual delay.
    if isinstance(provider, ReplayProvider):
        return provider.propose(trigger, candidates, at)
    # Killable process, rather than a thread whose blocked call survives timeout.
    # Custom providers must be fork-safe; production HTTP clients also need transport deadlines.
    ctx = multiprocessing.get_context('fork')
    receive, send = ctx.Pipe(duplex=False)
    def invoke():
        receive.close()
        try:
            send.send(provider.propose(trigger, candidates, at))
        except Exception as exc:
            send.send(ProviderResult(None, 'provider_transport_failure:' + type(exc).__name__))
        finally:
            send.close()
    process = ctx.Process(target=invoke, daemon=True)
    process.start(); send.close()
    try:
        if receive.poll(timeout_ms / 1000):
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
