export function actionStatus(status:string):string {
  return ({proposed:'Действие предложено',accepted:'Действие разрешено',rejected:'Действие отклонено',
    reserved:'Recovery разрешён: билет сохранён',queued:'Recovery разрешён: ожидает worker',
    running:'Recovery выполняется',claimed:'Recovery выполняется',submitted:'Запуск передан runtime',
    await_verify:'Worker завершил попытку: ожидается verification',verified:'Verification прошёл',
    human_review:'Нужен человек: recovery не подтверждён или не помог',
    revoked:'Execution завершён: разрешение отозвано'} as Record<string,string>)[status] ?? status;
}
