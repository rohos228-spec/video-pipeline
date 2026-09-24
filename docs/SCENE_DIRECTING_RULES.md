# Режиссура монтажной фразы — правила для «Улучшить сцену»

Кнопка **Монтаж → Улучшить сцену** гонит одну VO-ячейку через 3 ноды
группы `script_frames_qc` (`app/services/montage_scene_improve.py`):
`fw_action` → `fw_shots` → `fw_qc`. `fw_script` и `fw_check_script`
не зовём. В GPT только заказ оператора и реестр персонажей: якоря, биты,
паспорт и закадр как сюжет не входят. Закадр режет код после кадров.

## Поток

| нода | что делает на ячейке | GPT |
|---|---|---|
| fw_action | полное действие каждого кадра по заказу; без заказа — нарезка по закадру | да, если есть заказ; якоря/VO не в payload |
| fw_shots | шаг → кадр: роль, объект, план, ракурс, движение, стык | да; нет ответа → таблица камеры; VO не в payload |
| fw_qc | код чинит (повторы, 30°, предмет не с плеча, закадр кадров = куски ячейки); брак остался → GPT | `shots_qc_ru` только при браке |
| characters | реестр ∩ (заказ + закадр) | нет (код) |

Затем: кадры вставляются без перенумерации соседей, покрытие каждого шота
пишется в те же ключи, что чипы План / Ракурс / Движение / Стык.
Картинки кнопка не запускает.

## Правила и источники

| правило | источник |
|---|---|
| Establishing / вход в место до действия; re-establishing после сложной серии | Continuity editing — [Wikipedia](https://en.wikipedia.org/wiki/Continuity_editing), [LibreTexts](https://human.libretexts.org/Courses/Nashville_State_Community_College/Tokyo_in_Film/04%3A_Post-Production/4.03%3A_Editing_and_Animation/4.3.04%3A_Continuity_Editing) |
| Match on action: резать в середине движения, следующий кадр продолжает жест | [Wikipedia](https://en.wikipedia.org/wiki/Continuity_editing), [Learn About Film](https://learnaboutfilm.com/film-language/sequence/) |
| Чистый вход/выход из кадра; ушёл вправо → входит слева; погоня в одну сторону | [180-degree rule](https://en.wikipedia.org/wiki/180-degree_rule), [Jenny Stark: clean entrance/exit](http://jenny-stark-23rk.squarespace.com/new-page-95) |
| 180°: двое — камера по одну сторону линии; «восьмёрка» взглядами навстречу | [180-degree rule](https://en.wikipedia.org/wiki/180-degree_rule), [ВШРиС: правила по Кулешову](https://kinoshkola.org/articles/316b3fdb-e25f-464c-b045-c29c2bb69164) |
| 30° / 20 мм: два кадра одного объекта — другой план или ракурс, иначе jump cut | [30-degree rule](https://en.wikipedia.org/wiki/30-degree_rule), Filmmaker's Handbook ch.13 |
| Крупность «через план» (исключения: деталь↔крупный, дальний↔общий) | [С. Шинкарев: принципы монтажа](https://www.shinkareff.com/printsipy-montazha/), [mabuk.ru: 10 принципов](https://mabuk.ru/content/desyat-printsipov-montazha) |
| Eyeline: посмотрел → что увидел | [Wikipedia](https://en.wikipedia.org/wiki/Continuity_editing), Smith (Birkbeck) |
| Cut-in / cutaway / reaction shot (безмолвный крупный план реакции) | J. Mascelli, *The Five C's of Cinematography* (глава Close-ups) |
| Reaction shot: «дверь открылась — сначала лица в комнате» | [Hitchcock, «My own methods», BFI](https://www.bfi.org.uk/sight-and-sound/features/alfred-hitchcock-my-own-methods) |
| Размер объекта в кадре = его важность сейчас (важная вещь → деталь) | [Hitchcock's rule](https://www.filmmakersacademy.com/glossary/hitchcocks-rule/) |
| Саспенс: зритель знает раньше героя → второй персонаж показан до встречи | [Bordwell: bomb under the table](https://www.davidbordwell.net/blog/2013/11/29/hitchcock-lessing-and-the-bomb-under-the-table/) |
| Приоритет при конфликте: эмоция > история > ритм > след взгляда > 180° > пространство | W. Murch, *In the Blink of an Eye* — [Rule of Six](https://nofilmschool.com/2016/11/6-rules-good-cutting-according-oscar-winning-editor-walter-murch) |
| Монтажная фраза = группа кадров законченного действия | [textzone: фраза монтажная](https://textzone.ru/publ/slovar_sozdatelja_mediateksta/f/fraza_montazhnaja/86-1-0-262) |
| Эллипсис: рутину не расписывать, «войти поздно — выйти рано» | Hitchcock / Truffaut (анализ и синтез времени) |

## Пример

Промт «ткач кладёт носок → девушка кричит»:

1. действие · ДЕТАЛЬ · сверху · статика · cut — кладёт носок на пол
2. действие · СРЕДНИЙ · 3/4 · статика · cut — девушка кричит и убегает

Без промта — нарезка по закадру, GPT не зовём.

## Закадр

Сюжет из закадра не берём. После кадров код режет текст ячейки на число
шотов (`split_vo_for_shots`): сумма кусков = весь закадр, дыр нет.

## Отображение на доске

После улучшения доска сбрасывает очередь правок покрытия по кадрам сцены
и перечитывает доску. Место и окружение живут внутри действия кадра,
общей карточки паспорта нет.

Якоря на доске по-прежнему живут как метки текста (`scene_anchor_bits`),
но в прогон «Улучшить сцену» не входят.

## Ограничения

- Число кадров = события заказа, не длина закадра. Шаги `→` в промте
  задают отдельные кадры.
- Сумма закадра кадров = весь закадр ячейки (правило scene_design);
  при достройке кусок кадра может быть короче 13 знаков, но не пустой.
