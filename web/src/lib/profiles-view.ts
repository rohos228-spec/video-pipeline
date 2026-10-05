export function profileStatus(status:string):string {
  return ({approved:'Одобрен для следующей попытки',reserved:'Попытка зарезервирована',active:'Профиль применяется',
    applied:'Исполнитель завершил попытку; итог определит проверка',failed:'Попытка не помогла; нужен человек',
    cancelled:'Конфигурация возвращена; данные и бюджет сохранены'} as Record<string,string>)[status]??status;
}
