import type {Diagnosis, DiagnosticIncident} from './observations';

export function groupIncidents(incidents: DiagnosticIncident[]) {
  return {
    attention: incidents.filter(i=>i.status!=='resolved_confirmed' && i.currently_relevant),
    repaired: incidents.filter(i=>i.status==='resolved_confirmed' && i.resolution?.kind==='format_recovery'),
    other: incidents.filter(i=>i.status==='resolved_confirmed' && i.resolution?.kind!=='format_recovery'),
  };
}
export function diagnosisSummary(diagnosis: Diagnosis | null): string {
  if(!diagnosis) return 'Диагностика ещё не подтверждена.';
  if(!diagnosis.available) return 'Диагностика недоступна; показанные выводы могут быть устаревшими.';
  if(diagnosis.lagging) return 'Диагностика отстаёт от журнала; текущая картина не подтверждена.';
  if(!diagnosis.evidence_complete) return `Доказательства неполны. Выявлено блокирующих проблем: ${diagnosis.blocking_count}.`;
  return diagnosis.blocking_count ? `Требуют внимания блокирующие проблемы: ${diagnosis.blocking_count}.` : 'По доступным правилам блокирующих проблем не выявлено; качество результата не оценивалось.';
}
