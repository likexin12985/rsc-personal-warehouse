"""Unpublished original-input registry composed after all condition guards."""
from pathlib import Path
from sqlalchemy.dialects.postgresql import dialect
from sqlalchemy.schema import CreateTable
from app.return_condition_request_schema import define, NAME


def statements(metadata):
    table=define(metadata)
    result=[str(CreateTable(table).compile(dialect=dialect())),
        'REVOKE ALL ON TABLE public.'+NAME+' FROM PUBLIC,star_oam_api,star_oam_projector,star_oam_edge,edge_inbox,star_oam_backup',
        Path(__file__).with_suffix('.sql').read_text()]
    for name in (NAME,'stock_condition_events','stock_condition_cases','stock_condition_files','stock_condition_serials'):
        result.extend([f'CREATE CONSTRAINT TRIGGER condition_input_complete AFTER INSERT ON public.{name} '
            'DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.rsc_condition_request_input_fence()',
            f'ALTER TABLE public.{name} ENABLE ALWAYS TRIGGER condition_input_complete'])
    return table,result
