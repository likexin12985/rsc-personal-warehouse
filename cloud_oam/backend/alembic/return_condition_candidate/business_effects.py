"""Unpublished complete business effects; compose after condition key guards."""
from pathlib import Path


def statements():
    result = [Path(__file__).with_suffix('.sql').read_text()]
    for name in ('stock_condition_cases', 'stock_condition_events', 'audit_events',
                 'state_transition_events', 'outbox_events', 'notification_events',
                 'notification_person_targets'):
        result.extend([
            f'CREATE TRIGGER condition_effect_lock BEFORE INSERT OR UPDATE OR DELETE ON public.{name} '
            'FOR EACH ROW EXECUTE FUNCTION public.rsc_condition_effect_lock()',
            f'ALTER TABLE public.{name} ENABLE ALWAYS TRIGGER condition_effect_lock',
            f'CREATE CONSTRAINT TRIGGER condition_effect_complete AFTER INSERT OR UPDATE OR DELETE ON public.{name} '
            'DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.rsc_condition_effect_fence()',
            f'ALTER TABLE public.{name} ENABLE ALWAYS TRIGGER condition_effect_complete',
        ])
    return result
