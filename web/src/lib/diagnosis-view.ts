import type {Diagnosis, DiagnosticIncident} from './observations';

export function groupIncidents(incidents: DiagnosticIncident[]) {
  return {
    attention: incidents.filter(i=>i.status!=='resolved_confirmed'),
    repaired: incidents.filter(i=>i.status==='resolved_confirmed' && ['format_recovery','supervisor_recovery_verified'].includes(i.resolution?.kind ?? '')),
    other: incidents.filter(i=>i.status==='resolved_confirmed' && !['format_recovery','supervisor_recovery_verified'].includes(i.resolution?.kind ?? '')),
  };
}
export function diagnosisSummary(diagnosis: Diagnosis | null): string {
  if(!diagnosis) return 'Диагностика ещё не подтверждена.';
  if(!diagnosis.available) return 'Диагностика недоступна; показанные выводы могут быть устаревшими.';
  if(diagnosis.lagging) return 'Диагностика отстаёт от журнала; текущая картина не подтверждена.';
  if(!diagnosis.evidence_complete) return `Доказательства неполны. Выявлено блокирующих проблем: ${diagnosis.blocking_count}.`;
  return diagnosis.blocking_count ? `Требуют внимания блокирующие проблемы: ${diagnosis.blocking_count}.` : 'По доступным правилам блокирующих проблем не выявлено; качество результата не оценивалось.';
}
