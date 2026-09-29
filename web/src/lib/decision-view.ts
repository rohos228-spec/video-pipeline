export function decisionStatus(status:string):string {
  const labels:Record<string,string>={ready:'Ожидает решения',model_pending:'Модель получает контекст',
    acting:'Policy разрешила действие',waiting_worker:'Ожидает worker и verify',done:'Результат проверен',human_review:'Нужен человек'};
  return labels[status]??`Неизвестное состояние: ${status}`;
}
