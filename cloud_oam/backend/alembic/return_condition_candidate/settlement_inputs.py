"""Candidate installation after initial condition guards, not a release revision."""
from pathlib import Path
from sqlalchemy.dialects.postgresql import dialect
from sqlalchemy.schema import CreateTable
from app.return_condition_settlement_input_schema import define, NAME, SCANS


def statements(metadata):
    tables = define(metadata)
    result = []
    for table in tables:
        result += [str(CreateTable(table).compile(dialect=dialect())),
            f'REVOKE ALL ON TABLE public.{table.name} FROM PUBLIC,star_oam_api,star_oam_projector,star_oam_edge,edge_inbox,star_oam_backup',
            f'GRANT SELECT,INSERT ON TABLE public.{table.name} TO star_oam_api',
            f'GRANT SELECT ON TABLE public.{table.name} TO star_oam_backup']
    result.append(Path(__file__).with_name('settlement_inputs.sql').read_text())
    for name in (NAME, SCANS):
        result += [f'CREATE TRIGGER condition_settlement_input_immutable BEFORE UPDATE OR DELETE ON public.{name} '
            'FOR EACH ROW EXECUTE FUNCTION public.rsc_condition_reject_mutation()',
            f'CREATE TRIGGER condition_settlement_input_no_truncate BEFORE TRUNCATE ON public.{name} '
            'FOR EACH STATEMENT EXECUTE FUNCTION public.rsc_condition_reject_mutation()',
            f'CREATE TRIGGER condition_settlement_input_capture BEFORE INSERT ON public.{name} '
            'FOR EACH ROW EXECUTE FUNCTION public.rsc_condition_capture_settlement_input()']
        result += [f'ALTER TABLE public.{name} ENABLE ALWAYS TRIGGER condition_settlement_input_{suffix}'
            for suffix in ('immutable', 'no_truncate', 'capture')]
    for name in (NAME, SCANS, 'stock_condition_events', 'stock_condition_cases', 'stock_condition_files', 'stock_condition_serials'):
        result += [f'CREATE CONSTRAINT TRIGGER condition_settlement_input_complete AFTER INSERT ON public.{name} '
            'DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.rsc_condition_settlement_input_fence()',
            f'ALTER TABLE public.{name} ENABLE ALWAYS TRIGGER condition_settlement_input_complete']
    return tables, result
