ШАБЛОН ДЛЯ СОЗДАНИЯ IMAGE PROMPTS

СТИЛЬ: Trash Polka Noir Comic Grunge Poster Illustration

ВЕРСИЯ: v2.5 — с контролем переднего плана, запретом повторяющихся предметов в ближайших трех кадрах, запретом медных/металлических тазов, ослабленной object-clue логикой, запретом красных кругов, лимитом prompt до 5000 символов, правилом логичного русского текста на заметках, листах, бумагах, дневниках, медицинских записях и плакатах, запретом синонимичных сцен без прямой необходимости, запретом нефизического текста на экране и запретом повтора текста на изображении у ближайших 10 сцен

НАЗНАЧЕНИЕ:

Шаблон фиксирует только визуальный стиль, композиционную дисциплину и правила генерации image prompt.

Шаблон не фиксирует коридор, силуэт, умывальник, таз, стену расследования, культовую тему, конкретную сцену или повторяющийся реквизит.

Все сюжетные элементы, предметы, персонажи, локации и действия — переменные и могут использоваться только если они явно есть в исходных данных кадра.

ГЛАВНОЕ ИЗМЕНЕНИЕ v2.5:

1. Передний план больше не используется как обязательное место для важного предмета.

2. Кадр строится через среднюю дистанцию, позу, свет, архитектуру, фактуру и контекст, а не через крупный объект у нижнего края изображения.

3. Запрещено автоматически добавлять крупные foreground props: тазы, миски, чаши, лампы, столы, документы, оружие, книги, карты, улики, мебель или случайный реквизит у переднего края кадра.

4. Запрещены медные тазы, латунные тазы, металлические чаши, умывальные тазики, washbasin, washstand и любые повторяющиеся сосуды, если они прямо не указаны в исходных данных.

5. Введено правило неповтора: один и тот же заметный предмет, тип реквизита, мебель, сосуд, композиционный прием или foreground-формула не должны повторяться в ближайших трех кадрах подряд, если исходные данные не требуют прямой сюжетной непрерывности.

6. Контекстная логика должна передаваться через позу, свет, расстояние между объектами, среду, историческую фактуру и последствия действия, а не через случайный “предмет-улику”.

7. Детективная доска, стена расследования, линии, нити и evidence-board логика запрещены как постоянный визуальный прием. Такой кадр допустим не чаще 5% от общего числа кадров и только если это прямо требуется исходными данными.

8. Красные акценты не должны превращаться в круги, мишени, evidence marks, стрелки или обводки вокруг улик.

9. Каждый готовый prompt для одного кадра должен быть не длиннее 5000 символов вместе с пробелами. Целевой безопасный лимит — до 4977 символов.

10. Запрещено добавлять вымышленные детали. Каждый предмет, жест, след, документ, фон и визуальный акцент должен быть связан с исходным текстом ролика, voiceover, временем, местом и данными кадра.

11. Если в кадре есть заметки, листы, бумаги, дневники, медицинские записи, подписи или плакаты, они должны содержать логичный русский текст, соответствующий типу носителя и смыслу ближайших пяти текстовых фрагментов ролика. Запрещены одиночные слова, случайные словосочетания и бессмысленный псевдотекст.

12. Запрещены синонимичные сцены, если нет прямой сюжетной, монтажной или смысловой необходимости создать именно синонимичную сцену. Нельзя делать новый кадр только как перефразирование предыдущего или ближайшего кадра.

13. Запрещён любой текст на экране, который не является физическим текстом на бумаге, документе, дневнике, медицинской записи, плакате, вывеске, табличке, этикетке, книге, газете, карте, билете или другом реальном предмете внутри сцены.

14. Запрещено повторять один и тот же текст на изображении у ближайших 10 сцен, если это не требуется прямой сюжетной непрерывностью одного и того же физического предмета.

ОБЯЗАТЕЛЬНАЯ ЛОГИКА РАБОТЫ С ШАБЛОНОМ:

1. Нельзя удалять исходную структуру шаблона.

2. Нельзя удалять незаполненные поля.

3. Нельзя удалять пустые строки, технические блоки, правила, стиль, negative prompt, мини-форму или примеры, если пользователь прямо не попросил сократить ответ.

4. Если пользователь просит “заполнить шаблон”, нужно вернуть полный шаблон с сохранением всех блоков.

5. Заполнять можно только те поля, для которых во входных данных есть явная информация.

6. Запрещено заменять отсутствующие данные догадками.

7. Запрещено придумывать новые кадры, новые сцены, новые prompt-блоки или дополнительные варианты.

8. Запрещено автоматически создавать PROMPT_2 или PROMPT_3 по аналогии с PROMPT_1.

9. Если данных для поля нет, поле остается пустым или помечается как: нет исходных данных для заполнения.

10. Если пользователь просит “только готовые промты”, можно вывести только заполненные prompt-блоки, но нельзя создавать несуществующие кадры.

ЖЕЛЕЗНОЕ ПРАВИЛО ДЛИНЫ ГОТОВОГО PROMPT:

1. Каждый готовый prompt для одного кадра должен быть не длиннее 5000 символов вместе с пробелами.

2. Рабочий безопасный лимит: до 4977 символов вместе с пробелами.

3. Если prompt получается длиннее 4977 символов, его обязательно сжать до лимита до финального вывода.

4. При сокращении удалять повторы, воду, повторные перечисления стиля, дубли negative prompt, одинаковые запреты и лишние пояснения.

5. Нельзя удалять суть кадра: главный субъект, место, время, действие, фокус, свет, палитру, стиль, историческую достоверность, контекстную логику, правило логичного русского текста на заметках/листах/бумагах/плакатах, запрет бессмысленного текста, запрет нефизического текста на экране, запрет повтора текста на изображении у ближайших 10 сцен, запрет синонимичных сцен без необходимости, запрет вымышленных деталей и правило неповтора.

6. Запрещено отдавать prompt длиннее 5000 символов при любых условиях.

7. Перед финальным выводом обязательно проверить длину каждого prompt вместе с пробелами.

ПРАВИЛО КОНТРОЛЯ ПЕРЕДНЕГО ПЛАНА:

1. Не заставлять модель помещать важный предмет у переднего края изображения.

2. По умолчанию главный субъект должен находиться в читаемой центральной зоне или в среднем плане.

3. Передний план должен быть минимальным, спокойным и не доминировать над сценой.

4. Запрещено автоматически добавлять крупные предметы у нижнего края кадра.

5. Запрещено использовать foreground object как стандартную замену контекстной логики.

6. Не использовать формулу: “крупная вещь на переднем плане + персонаж вдали”, если это прямо не требуется исходным кадром.

7. Глубина сцены должна строиться через архитектуру, свет, позу, расстояние, фактуру стен, пола, дверей, окон, мебели и окружения, а не через случайный реквизит перед камерой.

8. Если исходные данные не требуют предмет на переднем плане, не добавлять его.

9. Если предмет действительно нужен по исходным данным, он должен быть пропорциональным, логичным и не перекрывать главного субъекта.

10. Не использовать крупные документы, миски, чаши, тазы, лампы, столы, книги, ножи, карты, улики, перчатки, ключи или мебель как автоматический foreground anchor.

ПРАВИЛО ЗАПРЕТА МЕДНЫХ ТАЗОВ И ПОВТОРЯЮЩИХСЯ СОСУДОВ:

1. Запрещены copper basin, brass basin, metal basin, washbasin, washstand, metal bowl, old bowl, water bowl, washing bowl, таз, медный таз, латунный таз, металлическая миска, умывальный таз, умывальник и любые похожие сосуды, если они прямо не указаны в исходных данных.

2. Не использовать таз, чашу, миску, умывальник или сосуд как универсальный исторический реквизит.

3. Не заменять “материальную историческую фактуру” металлическим тазом, чашей, кувшином или умывальником.

4. Не использовать scratched metal, metal patina и moisture stains так, чтобы они автоматически создавали металлический таз или умывальный предмет.

5. Если в исходном кадре есть старое помещение, больница, комната, архив, монастырь, казарма или кабинет, это не является основанием добавлять таз, миску или умывальник.

6. Если сосуд прямо указан в исходных данных, он может появиться только один раз и только в логичном месте, без увеличения до доминирующего foreground-объекта.

ПРАВИЛО НЕПОВТОРА В БЛИЖАЙШИХ ТРЕХ КАДРАХ:

1. Не повторять один и тот же заметный предмет, тип реквизита, мебель, сосуд, лампу, стол, стул, окно, дверной проем, стопку документов, карту, доску, металлический объект, foreground-композицию или визуальную формулу в ближайших трех кадрах подряд.

2. Если заметный предмет появился в кадре, не использовать его как крупный или центральный элемент в следующих трех кадрах, если исходные данные прямо не требуют продолжения той же сцены.

3. Если в одном кадре был стол у переднего края, в ближайших трех кадрах не использовать снова стол у переднего края.

4. Если в одном кадре был сосуд, чаша, таз, миска, умывальник или металлический предмет, в ближайших трех кадрах не использовать похожий сосуд или металлический foreground-объект.

5. Если в одном кадре была доска, карта, нити, документы или архивная поверхность, в ближайших трех кадрах не повторять ту же investigative visual formula.

6. Повтор допускается только при прямой сюжетной непрерывности: один и тот же объект явно указан в исходных данных нескольких последовательных кадров.

7. При необходимости визуального разнообразия менять не реквизит ради красоты, а способ раскрытия сцены: дистанцию камеры, позу, свет, архитектуру, фон, фактуру, направление взгляда, пустое пространство, силуэт или отношение персонажа к среде.

8. Если данных для нового предмета нет, лучше оставить сцену более пустой, чем добавлять случайный повторяющийся объект.

ПРАВИЛО ЗАПРЕТА СИНОНИМИЧНЫХ СЦЕН:

1. Запрещено создавать синонимичные сцены, если нет прямой необходимости создать именно синонимичную сцену.

2. Синонимичная сцена — это кадр, который повторяет смысл, действие, визуальную функцию, эмоциональную задачу, композиционный прием или предметную логику ближайших сцен, но описан другими словами.

3. Запрещено создавать новый prompt, если он отличается от ближайших кадров только синонимами, перестановкой слов, похожим настроением или заменой одного общего описания на другое.

4. Нельзя повторять один и тот же тип сцены без прямой необходимости: одинокий персонаж в комнате, человек у окна, пустой коридор, персонаж смотрит на документ, герой стоит в дверях, темная комната с бумагами, архивная атмосфера, следы расследования, один предмет как улика, общий план улицы, пустая больничная палата, стол с документами, стена с фактурой, силуэт в полумраке.

5. Если ближайшие сцены уже показывали тот же смысл, текущий кадр должен дать новый физически видимый смысл: другую фазу действия, другое положение тела, другое расстояние между персонажами, другой результат, другой поддержанный исходными данными предмет, другую архитектурную зону, другой ракурс или другую причинно-следственную деталь.

6. Синонимичная сцена разрешена только если:

* повтор прямо требуется voiceover или исходными данными;
* повтор показывает развитие состояния;
* повтор показывает «до / после»;
* повтор нужен для прямой сюжетной непрерывности;
* повтор фиксирует последствия уже показанного действия;
* один и тот же объект должен оставаться в кадре по логике сцены.

7. Даже если синонимичная сцена необходима, она не должна быть визуальной копией. Нужно изменить план, ракурс, позу, композиционные слои, световую функцию, расстояние, фокус или видимый результат, не добавляя вымышленных деталей.

8. Если прямой необходимости нет, лучше сделать более простую, пустую и точную сцену, чем создавать синонимичный кадр ради заполнения.

9. Перед финальным выводом prompt нужно проверить: не является ли он перефразированием ближайших сцен без нового видимого смысла.

ПРАВИЛО ПРОТИВ ПОСТОЯННОЙ DETECTIVE BOARD / СТЕНЫ РАССЛЕДОВАНИЯ:

1. Не превращать каждый кадр в detective board, investigation wall, evidence board, cork board, clue map, board with strings, board with red lines или стену расследования.

2. Такие изображения допустимы не чаще чем в 5% от общего числа кадров и только в явно детективных сценах, где доска прямо нужна по контексту.

3. Если используется доска расследования, она должна быть реальным физическим объектом внутри сцены.

4. Линии, нити, карточки и связи могут быть только на самой доске или рабочей поверхности, а не поверх всей сцены как графический интерфейс.

5. Запрещены красные линии, стрелки, круги, target marks и evidence strings, наложенные поверх персонажей, лиц, улиц, комнат, документов или фона.

6. В обычных кадрах логика расследования передается через позу, свет, предметы сцены, следы, дистанцию между объектами, бумаги с логичным русским текстом по контексту ближайших пяти фрагментов, направление взгляда, композицию и причинно-следственные детали.

7. Если в исходных данных нет доски, стены, карточек, нитей или прямого указания на investigation board, их нельзя добавлять.

МАРКЕРЫ КОНТЕКСТНОЙ ЛОГИКИ:

Использовать эти фразы как смысловые маркеры при составлении prompt, но не как видимый текст на изображении:

следы ведут к; всё начиналось с; история оказалась запутаннее; цепочка событий сходится; бытовая деталь становится важной только если она есть в исходных данных; поздняя легенда спорит с фактом; причина проявляется через последствия; прошлое оставило материальный след; версия не совпадает с реальностью; связь между событиями становится видимой через позу, свет и среду; расследование держится на мелких деталях, но не требует случайного предмета на переднем плане; контекст раскрывается через пространство; обрывок факта меняет смысл сцены; сцена объясняет, почему следствие пошло дальше; визуальная логика ведёт зрителя от действия к причине; каждый предмет подтверждает контекст ролика; факты складываются не сразу; внешне простая сцена скрывает причинную связь; бытовой след важнее красивого символа; событие читается через последствия; деталь связывает прошлое и текущий кадр; образ должен работать как улика без буквального указателя; напряжение возникает из несостыковки; зритель должен почувствовать, что за кадром есть проверяемая цепочка событий.

ПРАВИЛО КОНТЕКСТНЫХ ДЕТАЛЕЙ И ЗАПРЕТА ВЫМЫСЛА:

1. На изображении должны быть визуальные детали, связанные с текстом ролика, voiceover и данными кадра.

2. Запрещено делать пустую атмосферную картинку без связи с контекстом.

3. Каждый важный предмет, поза, жест, световой акцент и фон должны помогать понять, что происходит в сцене.

4. Детали можно брать только из исходных данных кадра, текста ролика, voiceover, локации, времени, исторической эпохи и описанного конфликта.

5. Запрещено добавлять вымышленные предметы, новых персонажей, новые документы, символы, карты, оружие, полицейские детали, современные элементы, случайные улики или новые сюжетные подсказки, которых нет в исходных данных.

6. Если контекст требует следов расследования, использовать только допустимые материальные признаки: изношенные поверхности, позы, дистанцию между объектами, бумаги с логичным русским текстом по контексту ближайших пяти текстов, предметы эпохи и детали окружения.

7. Нельзя добавлять декоративные детали ради атмосферы. Любая деталь должна иметь логическую связь с кадром.

8. Логика сцены важнее декоративности: изображение должно объяснять контекст, а не просто выглядеть детективным.

9. Если данных для детали нет, деталь не добавляется.

ПРАВИЛО ЗАПРЕТА НЕФИЗИЧЕСКОГО ТЕКСТА НА ЭКРАНЕ:

1. Запрещён любой текст на экране, если он не является физической частью реального предмета внутри сцены.

2. Разрешённый текст может быть только на:

* бумаге;
* письме;
* документе;
* дневнике;
* медицинской записи;
* книге;
* газете;
* плакате;
* вывеске;
* табличке;
* этикетке;
* карте;
* билете;
* упаковке;
* фотографии;
* физическом экране устройства, если такое устройство прямо указано в исходных данных и соответствует эпохе.

3. Строго запрещены:

* титры;
* субтитры;
* всплывающие надписи;
* поясняющие подписи;
* текст поверх изображения;
* графические комментарии;
* авторские ремарки;
* интерфейсные подписи без физического устройства;
* философские фразы поверх кадра;
* текст как дизайнерский слой;
* закадровый текст, написанный на изображении;
* любые слова, не прикрепленные к реальному предмету внутри сцены.

4. Нельзя писать “text on screen”, “caption”, “subtitle”, “overlay text”, “floating text”, “graphic label”, “written across the image”, “words on the image”.

5. Если смысл требует текста, он должен быть описан как физический текст на конкретном носителе: “на листе”, “в дневнике”, “на плакате”, “на табличке”, “на медицинской карточке”, “на газетной вырезке”.

6. Если физического носителя текста нет в исходных данных, текст не добавляется.

7. Любой текст должен быть визуально обоснован предметом, местом, эпохой и контекстом кадра.

ПРАВИЛО НЕПОВТОРА ТЕКСТА НА ИЗОБРАЖЕНИИ У БЛИЖАЙШИХ 10 СЦЕН:

1. Запрещено повторять один и тот же текст на изображении у ближайших 10 сцен.

2. Проверка выполняется по 5 предыдущим и 5 следующим сценам, если они известны.

3. Запрещено повторять:

* одинаковые слова на бумагах;
* одинаковые фразы в дневниках;
* одинаковые строки медицинских записей;
* одинаковые надписи на плакатах;
* одинаковые вывески;
* одинаковые таблички;
* одинаковые подписи;
* одинаковые даты;
* одинаковые номера;
* одинаковые газетные заголовки;
* одинаковые этикетки;
* одинаковые рукописные пометки.

4. Запрещено создавать серию кадров, где на разных бумагах, плакатах, заметках или документах повторяется один и тот же текст, если это не один и тот же физический предмет, продолжающийся по сюжету.

5. Повтор текста на изображении разрешён только если:

* один и тот же документ прямо продолжается в нескольких кадрах;
* один и тот же плакат, вывеска или табличка находится в одном и том же месте и должен оставаться по сценарию;
* повтор является важной уликой или мотивом, прямо указанным в исходных данных;
* повтор нужен для сравнения состояния одного и того же физического предмета.

6. Если требуется похожий тип носителя, текст должен быть другим и логически соответствовать текущему кадру, ближайшим пяти фрагментам voiceover и типу предмета.

7. Нельзя использовать универсальные повторяющиеся надписи вроде “дело”, “архив”, “пациент”, “секретно”, “дневник”, “улика”, “розыск”, “отчет”, “история болезни” в разных кадрах как шаблон.

8. Если нет точных исходных данных для текста, лучше описать частично читаемый, но логичный русский текст по контексту, чем придумывать повторяющуюся короткую надпись.

9. Запрещены одиночные слова и случайные словосочетания как способ обойти правило неповтора.

10. Перед финальным выводом prompt нужно проверить: не повторяет ли текст на изображении ближайшие 10 сцен.

ЖЕСТКОЕ ПРАВИЛО ДЛЯ КАДРА 1:

1. PROMPT ДЛЯ КАДРА 1 создается по основному шаблону PROMPT ДЛЯ ЗАПОЛНЕННОГО КАДРА.

2. Для PROMPT_1 можно использовать данные только из КАДРА 1.

3. Нельзя переносить в PROMPT_1 данные из КАДРА 2 или КАДРА 3.

4. Нельзя добавлять в PROMPT_1 несуществующие детали.

5. Если КАДР 1 не описан, PROMPT_1 не создается.

ЖЕСТКОЕ ПРАВИЛО ДЛЯ КАДРА 2:

1. PROMPT ДЛЯ КАДРА 2 не генерируется заново.

2. PROMPT ДЛЯ КАДРА 2 не создается по основному шаблону PROMPT ДЛЯ ЗАПОЛНЕННОГО КАДРА.

3. PROMPT ДЛЯ КАДРА 2 заполняется только прямым дословным копированием текста из двух исходных строк: строка 26 + строка 28.

4. Текст из строки 26 вставляется первым.

5. Текст из строки 28 вставляется вторым, сразу после текста из строки 26.

6. Между текстом строки 26 и текстом строки 28 сохраняется обычный перенос строки.

7. Нельзя переписывать, расширять, сокращать, стилизовать, адаптировать или дополнять текст для PROMPT_2.

8. Нельзя превращать текст из строки 26 + строки 28 в новый полноценный cinematic prompt.

9. Нельзя брать данные из КАДРА 1 для заполнения PROMPT_2.

10. Нельзя брать данные из общего описания стиля для заполнения PROMPT_2.

11. Нельзя использовать собственную фантазию для заполнения PROMPT_2.

12. Если второго кадра нет, PROMPT ДЛЯ КАДРА 2 не заполняется.

13. Если строки 26 или 28 пустые, отсутствуют или не относятся ко второму кадру, PROMPT ДЛЯ КАДРА 2 не заполняется.

14. Если пользователь не дал явно второй кадр, не создавай второй кадр и не создавай PROMPT_2.

ЖЕСТКОЕ ПРАВИЛО ДЛЯ КАДРА 3:

1. PROMPT ДЛЯ КАДРА 3 создается только если во входных данных явно есть КАДР 3.

2. Если КАДР 3 не указан, не описан или отсутствует, PROMPT_3 запрещен.

3. Нельзя создавать третий кадр автоматически.

4. Нельзя создавать третий кадр “для полноты”.

5. Нельзя создавать третий кадр “как продолжение сцены”.

6. Нельзя создавать третий кадр “по аналогии”.

7. Если КАДРА 3 нет, нужно указать: КАДР 3: нет исходных данных для заполнения.

КРАТКОЕ ОПИСАНИЕ СТИЛЯ:

Trash polka + dark comic book style with black, off-white, dirty cream, charcoal, dark gray and vivid blood-red accents; raw brush smears, ink splashes, spray-paint effects, distressed paper texture, ripped poster fragments, halftone dots, rough print imperfections, realistic material surfaces and intense high-contrast mixed media energy.

STYLE_LABEL =

Trash Polka Noir Comic Grunge Poster Illustration

STYLE_CORE =

trash polka + noir comic + graphic novel + grunge poster art + distressed printmaking + high-contrast mixed media illustration.

STYLE_LOCK_RULE =

not clean minimalist style, not photorealism, not glossy 3D, not cute, not pastel, not bright cheerful colors, not separate collage panels inside one image, not multiple visual frames inside one generated image, not automatic detective board, not investigation wall unless explicitly required by source data, not foreground-object formula, not repeated props, not copper basin, not brass basin, not washstand, not synonym scene without direct need, not non-physical screen text, not repeated image text across nearest 10 scenes.

QUALITY_VECTOR =

intense gritty cinematic impact, strong readable silhouette, poster-like composition, high visual tension, controlled chaotic energy, realistic historical-material grounding, clear context logic based only on source data, no random foreground clue object, no synonym scene without direct need, no repeated image text across nearest 10 scenes.

RENDERING_VECTOR =

raw brush smears, ink splashes, spray-paint effects, distressed paper, ripped poster fragments, halftone dots, rough print imperfections, gritty comic inking, graphic overlays, dynamic red slashes.

TEXTURE_VECTOR =

distressed printmaking, torn paper, rough ink, spray texture, halftone grain, dirty cream paper, grunge scratches, smeared charcoal shadows, analog print noise, damaged archive-paper surface, imperfect screenprint texture.

REALISM_TEXTURE_VECTOR =

realistic material texture, aged cracked plaster, chipped paint, worn dark wood grain, damp stone, dust in corners, mud traces, faded fabric, stained paper fibers, folded paper edges, old varnish, worn floorboards, rough wall surface, believable grime and tactile historical wear. Use scratched metal, metal patina and moisture stains only if directly supported by source data. Do not let them create basins, bowls, washstands or repeated metal vessels.

RED_GRAPHIC_RULE =

Use vivid blood-red accents only as rough brush slashes, jagged paint marks, smeared ink, torn poster fragments, print interference, distressed red scratches, spray texture and abstract graphic stress marks integrated into the environment. Do not use red circles, evidence circles, target rings, circular highlight marks, red outlines around clues, red arrows pointing at evidence or literal investigation-board markings.

LINEWORK_VECTOR =

gritty comic inking, bold dramatic framing, rough contour lines, heavy graphic shadow masses, expressive visual storytelling.

LIGHT_VECTOR =

exaggerated noir lighting, harsh backlight or cold side light, pale winter glow or controlled amber-gray interior light, deep shadows, strong silhouette separation, believable directional light.

COLOR_VECTOR =

black, off-white, dirty beige, dirty cream, charcoal, dark gray, muted amber-gray and vivid blood-red accents.

COMPOSITION_VECTOR =

one unified scene, not a collage; chaotic collage energy translated into integrated poster composition; single readable focal point; controlled cinematic framing; medium-distance composition by default; high visual tension; readable spatial depth without forcing large foreground objects; minimal unobtrusive foreground; no oversized props at the front edge; no repeated foreground-object formula; no detective board logic unless the source data explicitly requires a real physical board; no synonym scene without direct need.

FOREGROUND_CONTROL_RULE =

Keep the main subject in the midground or readable central area by default. Do not place large props, bowls, basins, furniture, documents, weapons, clue-like items, lamps, books, maps or symbolic objects at the front edge unless explicitly required by the source data. Context must be shown through posture, lighting, spatial relation, period environment and supported material details, not through a random object pushed toward the camera.

PROMPT_LENGTH_RULE =

every finished prompt must be no longer than 5000 characters including spaces; target limit is 4977 characters; compress repetitions, duplicated style lists, repeated negative rules and filler while preserving subject, location, time, action, focus, light, palette, style, context logic, historical accuracy, logical Russian text rules for notes/sheets/papers/posters when such items are source-supported, no meaningless text, no non-physical screen text, no repeated image text across nearest 10 scenes, no invented details, foreground control, synonym-scene control and three-frame repetition control.

DETECTIVE_BOARD_RULE =

do not turn normal scenes into detective boards, investigation walls, clue maps, cork boards, evidence boards, boards with strings or boards with red lines; such imagery is allowed in no more than 5% of frames and only when explicitly justified by the source context; if present, the board must be a real physical object inside the scene and all strings, cards and links must stay on that board, not overlaid across the image.

THREE_FRAME_REPETITION_RULE =

do not repeat the same distinctive prop, object type, furniture item, container, basin, bowl, metal vessel, document stack, lamp, chair, table, board, window setup or foreground composition in the next three consecutive frames unless the source data explicitly says the same object continues across those frames. If an object appeared as a noticeable visual element in one frame, avoid using it again as a major element in the following three frames. Change the visual logic through camera distance, posture, lighting, background texture, architecture and scene-specific details instead of repeating the same prop.

SYNONYM_SCENE_RULE =

do not create scenes that repeat the same meaning, visual function, action, mood, composition or object logic as nearby scenes with different wording only. A synonym scene is allowed only when the source data directly requires repetition, continuity, before/after comparison, development of state or consequence. If repetition is required, change camera distance, angle, posture, spatial relation, visible result, focal point or source-supported material detail without inventing new objects.

CONTEXT_LOGIC_VECTOR =

traces lead toward the cause; the story is more tangled than it first appears; factual traces conflict with later legend; the scene must reveal cause and consequence through posture, light, distance, period-correct environment and source-supported material details, without invented clues, without forcing a single object as a clue, without repeating nearby scenes as synonyms, and without pushing random evidence-like items into the foreground.

CONTEXT_DETAIL_RULE =

Every important visible detail must come from the frame data, voiceover, video context, location, time period or stated conflict. Do not add fictional clues, extra documents, new people, weapons, maps, police props, random symbols, decorative story hints, random foreground objects or repeated props. If a detail is not supported by the source data, leave it out.

MATERIAL_REALISM_RULE =

The scene must feel tactile, old, inhabited and physically believable. Surfaces may show paper fibers, dust, cracks, wood grain, worn fabric, dirty floor texture, chipped paint or age marks when supported by the source data. Do not overuse metal patina, scratched metal, bowls, basins, washstands, wet surfaces or moisture stains as default historical texture. Do not let material realism create recurring props that are not required by the frame.

SUBJECT_RULES =

any silhouette, historical figure, urban object, psychological thriller scene, investigation scene, archival scene, institutional room, abstract noir scene or documentary historical environment. Do not default to crime fragments, object-based clues, foreground evidence objects, bowls, basins, washstands, repeated prop-centered compositions or synonym scenes unless directly supported by source data.

TEXT_RULE =

If notes, sheets, papers, diaries, medical records, signatures, posters, labels or handwritten pages are present and supported by the source data, they must contain coherent Russian text appropriate to the object and based on the logic of the nearest five text fragments from the video/voiceover/frame context. Medical records must contain medical notes in Russian, a personal diary must contain a logical diary entry and signature when required, a poster must contain a typical Russian poster inscription. Do not write isolated single words, random word pairs, disconnected short phrases, meaningless pseudo-text, Latin filler or nonsensical text. Beautiful partially unreadable Russian handwriting or print-like text is allowed only as a natural visual texture, not as gibberish. Do not add any text on the image unless it is physically placed on a source-supported object such as paper, document, poster, sign, label, diary, medical record, newspaper, book, ticket or map. Do not repeat the same visible text across the nearest 10 scenes unless it is the same physical object continuing by direct source logic.

NON_PHYSICAL_SCREEN_TEXT_RULE =

no captions, no subtitles, no floating text, no overlay text, no graphic labels, no explanatory words over the image, no philosophical phrases over the frame, no author comments, no text as a design layer. Text is allowed only as physical writing or print on a real source-supported object inside the scene.

TEN_SCENE_IMAGE_TEXT_REPETITION_RULE =

before output, compare visible text planned for the current frame with the nearest 10 scenes: 5 previous and 5 next if known. Do not repeat the same words, phrases, diary lines, medical notes, poster inscriptions, signs, dates, labels, newspaper headlines or document markings unless the same physical object continues by direct source requirement. Use context-specific coherent Russian text instead of repeated generic words.

GORE_RULE =

psychological crime-thriller mood without gore, explicit violence or graphic injury unless the user directly requests otherwise.

HISTORICAL_DETAIL_RULE =

If the scene is historical, all objects, clothing, furniture, lighting sources and architecture must fit the period. Avoid modern hospital equipment, modern signs, modern clothing, plastic objects, contemporary furniture, digital screens, clean clinical interiors, random anachronistic details and generic historical props not supported by the frame.

ANTI_SYMBOL_RULE =

Do not rely on metaphorical symbols unless the user explicitly asks. Keep the scene grounded in material environment, human posture, architecture, light, texture, spatial tension and context logic from the source. Do not use investigation-board symbols, foreground clue objects, bowls, basins, recurring props, non-physical text overlays or synonym scenes as default storytelling devices.

RUSSIAN_CONTEXT_TEXT_RULE =

If the source-supported scene contains notes, sheets, papers, diaries, medical records, signatures, posters, labels or handwritten pages, those surfaces must contain coherent Russian text that logically matches the object type and the meaning of the nearest five text fragments from the video/voiceover/frame context. Medical records must look like medical notes in Russian; a personal diary must contain a diary-like Russian entry and a plausible signature if the source mentions a signature; a poster must contain a typical poster inscription in Russian. Do not use isolated single words, random word pairs, disconnected phrases, fake gibberish, meaningless pseudo-Cyrillic, Latin filler, lorem ipsum or nonsensical text. Beautiful partially unreadable Russian handwriting or print-like texture is allowed only when the text is too small to read clearly, but the prompt must still describe the content as coherent Russian writing based on nearby context. Do not place Russian text as captions, subtitles, overlays or floating design text. Do not repeat the same Russian text on image surfaces across the nearest 10 scenes unless the same physical object continues by direct source logic.

МИНИ-ФОРМА ДЛЯ КАДРА 1:

MAIN_SUBJECT =

SETTING =

TIME_PERIOD =

ACTION_OR_STATE =

NOIR_LIGHTING =

RED_GRAPHIC_ACCENTS =

REALISM_TEXTURES =

GRUNGE_TEXTURES =

COMIC_FRAME_ELEMENTS =

FOCAL_POINT =

MOOD =

CONTEXT_LOGIC =

FOREGROUND_USAGE =

REPETITION_CHECK_PREVIOUS_3_FRAMES =

SYNONYM_SCENE_CHECK_NEAREST_SCENES =

BOARD_USAGE =

HISTORICAL_OR_MATERIAL_DETAILS =

TEXT_RESTRICTIONS =

NON_PHYSICAL_SCREEN_TEXT_CHECK =

IMAGE_TEXT_REPETITION_CHECK_NEAREST_10_SCENES =

CONTEXT_SPECIFIC_NEGATIVES =

МИНИ-ФОРМА ДЛЯ КАДРА 2:

PROMPT_2_SOURCE_LINE_26 =

PROMPT_2_SOURCE_LINE_28 =

ВАЖНО:

КАДР 2 не заполняется через MAIN_SUBJECT, SETTING, TIME_PERIOD, ACTION_OR_STATE и другие поля.

КАДР 2 не превращается в новый полный prompt.

PROMPT_2 состоит только из дословного текста строки 26 + строки 28.

МИНИ-ФОРМА ДЛЯ КАДРА 3:

MAIN_SUBJECT =

SETTING =

TIME_PERIOD =

ACTION_OR_STATE =

NOIR_LIGHTING =

RED_GRAPHIC_ACCENTS =

REALISM_TEXTURES =

GRUNGE_TEXTURES =

COMIC_FRAME_ELEMENTS =

FOCAL_POINT =

MOOD =

CONTEXT_LOGIC =

FOREGROUND_USAGE =

REPETITION_CHECK_PREVIOUS_3_FRAMES =

SYNONYM_SCENE_CHECK_NEAREST_SCENES =

BOARD_USAGE =

HISTORICAL_OR_MATERIAL_DETAILS =

TEXT_RESTRICTIONS =

NON_PHYSICAL_SCREEN_TEXT_CHECK =

IMAGE_TEXT_REPETITION_CHECK_NEAREST_10_SCENES =

CONTEXT_SPECIFIC_NEGATIVES =

ВАЖНО:

КАДР 3 используется только если пользователь явно дал третий кадр.

Если третьего кадра нет, эта мини-форма не заполняется.

СТИЛЬ:

Trash Polka Noir Comic Grunge Poster Illustration

PROMPT ДЛЯ ЗАПОЛНЕННОГО КАДРА 1 ИЛИ ЯВНО СУЩЕСТВУЮЩЕГО КАДРА 3:

Create one unified scene, not a collage, not multiple visual panels inside one image, in a trash polka + noir dark comic book + graphic novel style.

Show [MAIN_SUBJECT] in [SETTING], during [TIME_PERIOD], [ACTION_OR_STATE], with a [MOOD] crime-thriller and psychological tension atmosphere. The scene must feel historically grounded, physically believable and material, not symbolic, not theatrical, not modernized.

Context logic: [CONTEXT_LOGIC]. Every visible detail must come from the source frame, video text, voiceover, location, time period or stated conflict. Do not add fictional clues, new objects, extra characters, random detective props, foreground clue objects or decorative story hints. Do not create a synonym scene that repeats the same meaning, action, composition, mood or visual function of nearby scenes unless the source data directly requires repetition or continuity.

Synonym scene control: [SYNONYM_SCENE_CHECK_NEAREST_SCENES]. The frame must not be only a reworded version of nearby scenes. If nearby scenes already show the same type of moment, change the visible phase of action, camera distance, angle, posture, spatial relation, architectural focus, supported material detail or physical result without inventing new objects.

Foreground control: [FOREGROUND_USAGE]. Keep the main subject in the midground or readable central area by default. Keep the foreground minimal and unobtrusive. Do not place large props, bowls, basins, documents, furniture, symbolic objects, weapons, maps, books or clue-like items at the front edge unless explicitly required by the source data. Build spatial depth through architecture, light, posture and supported environment details, not through random foreground objects.

Repetition control: [REPETITION_CHECK_PREVIOUS_3_FRAMES]. Do not repeat the same distinctive object, prop, furniture item, basin, bowl, metal vessel, document stack, lamp, table, chair, window setup or foreground composition from any of the previous three frames unless the source data explicitly requires continuity. If a similar object appeared recently, replace it with scene-specific posture, lighting, architecture, background texture or another source-supported detail.

Composition: use one cinematic frame with a single readable focal point: [FOCAL_POINT]. Use controlled cinematic framing and medium-distance composition by default. Build the scene with rule of thirds, strong silhouette design, bold noir framing and readable spatial depth. The chaotic trash polka energy must be integrated into one unified poster-like composition, not split into collage panels. Board usage: [BOARD_USAGE]. Do not create a detective board, investigation wall, clue map, strings or red connecting lines unless the source data explicitly requires a real physical board inside the scene.

Lighting: use [NOIR_LIGHTING] to separate the subject from the environment. Keep the light believable and directional, with cold side light, pale sky glow, controlled amber-gray interior tones, deep shadows and heavy graphic shadow masses.

Style: emphasize trash polka aesthetics through [GRUNGE_TEXTURES], raw brush smears, ink splashes, spray-paint effects, distressed paper texture, ripped poster fragments, halftone dots, rough print imperfections, analog print noise, high-contrast graphic overlays and gritty comic inking.

Red accents: use [RED_GRAPHIC_ACCENTS] only as jagged red brush slashes, smeared paint, distressed print interference, torn poster fragments, red ink splashes, rough scratches and abstract stress marks integrated into the environment. Do not use red evidence circles, target rings, circular highlight marks, red outlines around clues or literal investigation-board markings.

Realism texture: add [REALISM_TEXTURES] with tactile historical material detail: aged cracked plaster, chipped paint, worn wood grain, damp stone, dust, mud, faded fabric, stained paper fibers, folded edges, subtle grime and believable surface wear. Use scratched metal, metal patina or moisture stains only when explicitly supported by the source data. Do not create copper basins, brass basins, metal bowls, washbasins, washstands or recurring vessels.

Historical/material details: include [HISTORICAL_OR_MATERIAL_DETAILS]. All objects must belong to the scene and time period and must be supported by source data. Avoid random modern details, invented clues, extra documents, new props, clean clinical looks, plastic objects, contemporary furniture, digital screens, modern signage, generic historical props, washstands, basins, bowls or repeated foreground items.

Text rule: [TEXT_RESTRICTIONS]. Non-physical screen text check: [NON_PHYSICAL_SCREEN_TEXT_CHECK]. Image text repetition check: [IMAGE_TEXT_REPETITION_CHECK_NEAREST_10_SCENES]. If the scene includes source-supported notes, sheets, papers, diaries, medical records, signatures, posters, labels or handwritten pages, fill them with coherent Russian text based on the nearest five video/voiceover/frame text fragments. Medical papers must read as medical notes, diary pages as diary entries with a signature when required, posters as typical Russian poster inscriptions. Do not use isolated words, random word pairs, disconnected short phrases, Latin filler, lorem ipsum, fake pseudo-Cyrillic or meaningless text. Do not add captions, subtitles, overlay text, floating text, explanatory labels, author comments or any text not physically printed or written on a real object inside the scene. Do not repeat the same visible text from the nearest 10 scenes unless the same physical object continues by direct source requirement.

Final style lock: unified cinematic frame, trash polka, noir comic, graphic novel, grunge poster art, distressed printmaking, realistic historical texture, high-contrast mixed media illustration, gritty comic inking, rough contour lines, heavy shadow masses, halftone grain, dirty paper surface, raw brush smears, ink splashes, spray-paint distress, black, off-white, dirty cream, charcoal, dark gray, muted amber-gray and vivid blood-red slashes only, no red circles, no isolated words, no random word pairs, no meaningless pseudo-text, no Latin filler, contextual Russian text only on source-supported papers/notes/posters, no non-physical screen text, no captions, no subtitles, no overlay text, no repeated image text across nearest 10 scenes, no automatic detective board, no invented details, no large foreground props, no copper basin, no brass basin, no washstand, no repeated distinctive prop from the previous three frames, no synonym scene without direct need.

PROMPT ДЛЯ КАДРА 2:

[PROMPT_2_SOURCE_LINE_26]

[PROMPT_2_SOURCE_LINE_28]

ВАЖНО:

Это единственный допустимый формат PROMPT_2.

PROMPT_2 нельзя переписывать.

PROMPT_2 нельзя расширять.

PROMPT_2 нельзя превращать в полноценный cinematic prompt.

PROMPT_2 нельзя генерировать по стилевому шаблону.

PROMPT_2 должен быть только дословной склейкой строки 26 и строки 28 с переносом строки между ними.

NEGATIVE PROMPT ДЛЯ ЗАПОЛНЕННОГО КАДРА 1 ИЛИ ЯВНО СУЩЕСТВУЮЩЕГО КАДРА 3:

isolated single words, random word pairs, disconnected short phrases, repeated image text across nearest ten scenes, repeated paper text, repeated poster text, repeated diary text, repeated medical record text, repeated sign text, meaningless pseudo-text, fake pseudo-Cyrillic, Latin filler text, lorem ipsum, nonsensical handwriting, unrelated readable text, wrong-language text, captions, subtitles, overlay text, floating text, text across the image, graphic labels, explanatory labels, author comments, non-physical screen text, text not attached to a real object, logo, watermark, collage panels, multiple visual panels inside one image, synonym scene without direct source need, repeated scene meaning, reworded duplicate scene, same visual function as nearby scenes, evidence circles, red circles, target rings, circular highlight marks, red outlines around clues, red arrows pointing at evidence, literal evidence-board strings, automatic detective board, investigation wall, clue map, board with strings, board with red lines, invented clues, extra documents, new characters, random detective props, clean minimalist style, photorealism, glossy 3D render, cute style, pastel palette, bright cheerful colors, low detail, blurry, gore, explicit violence, modern objects, modern clothing, modern hospital equipment, fluorescent lighting, contemporary furniture, clean hospital look, surreal symbols, fantasy imagery, random table objects, large foreground objects, oversized props at the front edge, repeated foreground prop, repeated clue object, foreground basin, copper basin, brass basin, metal bowl, washbasin, washstand, recurring washstand, recurring metal vessel, recurring bowl, object pushed into camera, prop-centered composition, repeated furniture setup, same object repeated across consecutive frames, same foreground composition repeated within three frames, [CONTEXT_SPECIFIC_NEGATIVES]

NEGATIVE PROMPT ДЛЯ КАДРА 2:

Не создавать, если пользователь не дал отдельный negative prompt для кадра 2.

Не генерировать автоматически.

Не копировать negative prompt от кадра 1.

Не придумывать negative prompt для кадра 2.

Если нужен negative prompt для кадра 2, он должен быть явно дан пользователем.

ФОРМАТ ВЫВОДА, ЕСЛИ ЕСТЬ ТОЛЬКО КАДР 1:

КАДР 1 / PROMPT_1:

[заполненный prompt по шаблону PROMPT ДЛЯ ЗАПОЛНЕННОГО КАДРА 1]

КАДР 1 / NEGATIVE PROMPT_1:

[заполненный negative prompt]

КАДР 2 / PROMPT_2:

нет исходных данных для заполнения

КАДР 3 / PROMPT_3:

нет исходных данных для заполнения

ФОРМАТ ВЫВОДА, ЕСЛИ ЕСТЬ КАДР 1 И ДАННЫЕ ДЛЯ КАДРА 2 В СТРОКАХ 26 И 28:

КАДР 1 / PROMPT_1:

[заполненный prompt по шаблону PROMPT ДЛЯ ЗАПОЛНЕННОГО КАДРА 1]

КАДР 1 / NEGATIVE PROMPT_1:

[заполненный negative prompt]

КАДР 2 / PROMPT_2:

[дословный текст строки 26]

[дословный текст строки 28]

КАДР 3 / PROMPT_3:

нет исходных данных для заполнения

ФОРМАТ ВЫВОДА, ЕСЛИ ЕСТЬ КАДР 1, КАДР 2 И ЯВНО ДАН КАДР 3:

КАДР 1 / PROMPT_1:

[заполненный prompt по шаблону PROMPT ДЛЯ ЗАПОЛНЕННОГО КАДРА 1]

КАДР 1 / NEGATIVE PROMPT_1:

[заполненный negative prompt]

КАДР 2 / PROMPT_2:

[дословный текст строки 26]

[дословный текст строки 28]

КАДР 3 / PROMPT_3:

[заполненный prompt по шаблону PROMPT ДЛЯ ЗАПОЛНЕННОГО КАДРА 3]

КАДР 3 / NEGATIVE PROMPT_3:

[заполненный negative prompt]

КОНТРОЛЬНАЯ ПРОВЕРКА ПЕРЕД ОТВЕТОМ:

Перед финальным ответом проверь:

1. Не удалены ли исходные блоки шаблона.

2. Не удалены ли пустые поля.

3. Не создан ли PROMPT_2 как новый промт.

4. PROMPT_2 состоит только из строки 26 + строки 28.

5. Не добавлены ли в PROMPT_2 слова, которых не было в строках 26 и 28.

6. Не создан ли PROMPT_3 без явно данного третьего кадра.

7. Не скопированы ли данные КАДРА 1 в КАДР 2.

8. Не создан ли negative prompt для КАДРА 2 автоматически.

9. Если второго кадра нет, указано ли: нет исходных данных для заполнения.

10. Если третьего кадра нет, указано ли: нет исходных данных для заполнения.

11. Проверена ли длина каждого готового prompt: не более 5000 символов вместе с пробелами, целевой лимит до 4977 символов.

12. Удалены ли повторы, вода и дубли правил, если prompt был длиннее лимита.

13. Не превращен ли обычный кадр в detective board, investigation wall или доску с нитями без прямого основания в исходных данных.

14. Если доска расследования есть, является ли она физическим объектом внутри сцены, а не графической накладкой поверх изображения.

15. Есть ли в кадре контекстная логика из текста ролика, voiceover и данных кадра.

16. Не добавлены ли вымышленные предметы, новые персонажи, случайные улики, современные детали или декоративные подсказки без исходных данных.

17. Не создан ли крупный предмет на переднем крае кадра без прямого основания в исходных данных.

18. Не появился ли медный таз, латунный таз, металлическая миска, washbasin, washstand или похожий сосуд без прямого указания в исходных данных.

19. Не повторяется ли заметный предмет, сосуд, мебель, доска, лампа, окно, стол, документная стопка или foreground-композиция из предыдущих трех кадров.

20. Если повтор есть, подтвержден ли он прямой сюжетной непрерывностью в исходных данных.

21. Если в кадре есть заметки, листы, бумаги, дневники, медицинские записи, подписи или плакаты, задан ли для них логичный русский текст по ближайшим пяти текстовым фрагментам ролика.

22. Не появились ли одиночные слова, случайные словосочетания, бессмысленный псевдотекст, псевдокириллица, lorem ipsum, латинская заглушка или текст не на русском языке там, где требуется русский контекстный текст.

23. Не является ли текущая сцена синонимичной ближайшим сценам без прямой необходимости.

24. Если синонимичная сцена есть, подтверждена ли она прямой сюжетной необходимостью, развитием состояния, сравнением «до / после», последствием или продолжением одного и того же объекта.

25. Не появился ли текст на экране, который не является физическим текстом на бумаге, плакате, вывеске, табличке, документе, дневнике, медицинской записи, газете, книге, билете, карте, этикетке или другом реальном предмете.

26. Не появились ли титры, субтитры, overlay text, floating text, поясняющие подписи, авторские комментарии или текст как дизайнерский слой поверх изображения.

27. Не повторяется ли текст на изображении у ближайших 10 сцен.

28. Если текст на изображении повторяется, подтверждено ли, что это один и тот же физический предмет или прямой сюжетный мотив из исходных данных.

ПРИМЕР ЗАПОЛНЕНИЯ ДЛЯ СЦЕНЫ С ФРИДРИХОМ НИЦШЕ:

MAIN_SUBJECT =

Friedrich Nietzsche sitting alone in a late-19th-century psychiatric clinic room, physically fragile, withdrawn, with a remote tired stare, iconic large mustache, thinning hair, pale skin and dark worn period clothing.

SETTING =

a modest German psychiatric ward interior with a narrow iron bed, simple wooden chair, cracked plaster walls, closed wooden door and tall window. No washstand, no basin, no bowl, no metal vessel.

TIME_PERIOD =

late 1880s or early 1890s.

ACTION_OR_STATE =

seated on a simple wooden chair, slightly hunched, hands resting in his lap or loosely gripping the chair, silent and mentally distant.

NOIR_LIGHTING =

cold rain-washed side light from the window, pale winter sky glow, muted amber-gray interior tones, deep soft shadows and realistic chiaroscuro.

RED_GRAPHIC_ACCENTS =

rough blood-red brush slashes, distressed print interference, torn red poster fragments and smeared red ink stress marks integrated into the room, with no circles or target marks.

REALISM_TEXTURES =

aged cracked plaster, chipped paint, worn dark wood grain, thin institutional bedding, dusty corners, worn floor texture, faded fabric, paper fibers, rough ink scratches and analog print noise. No metal basin, no washstand, no bowl.

GRUNGE_TEXTURES =

distressed paper texture, halftone grain, rough print imperfections, smeared charcoal shadows, spray texture, ripped poster fragments and dirty cream paper.

COMIC_FRAME_ELEMENTS =

bold dramatic framing, gritty comic inking, rough contour lines, heavy graphic shadow masses, strong silhouette design and poster-like composition.

FOCAL_POINT =

Nietzsche seated alone in the room.

MOOD =

archival crime-thriller mood of control, silence, breakdown and disputed legacy.

CONTEXT_LOGIC =

the scene shows a material trace of breakdown and institutional control through posture, room details, period furniture and silence, without invented clues and without foreground clue objects.

FOREGROUND_USAGE =

minimal unobtrusive foreground, no large prop at the front edge, no basin, no bowl, no washstand, no document pile, no object pushed toward camera.

REPETITION_CHECK_PREVIOUS_3_FRAMES =

avoid repeating any distinctive object or foreground composition from the previous three frames; use posture, window light, empty wall space and institutional room texture instead.

SYNONYM_SCENE_CHECK_NEAREST_SCENES =

do not repeat a nearby scene of a lone figure in a dark institutional room unless the source requires it; if a similar scene exists nearby, distinguish this frame through Nietzsche’s specific seated posture, psychiatric clinic context, period room texture and visible institutional stillness.

BOARD_USAGE =

no detective board, no investigation wall, no strings, no clue map; context is shown through physical environment and posture.

HISTORICAL_OR_MATERIAL_DETAILS =

period-appropriate wooden chair, iron bed, old institutional bedding, cracked plaster, closed wooden door, tall window, no modern medical equipment, no modern lighting, no plastic objects, no washstand, no basin.

TEXT_RESTRICTIONS =

if papers, labels or medical forms are visible, they must contain coherent Russian text appropriate to the object type and nearby context; medical papers must look like Russian medical notes, with no isolated words, no random phrases and no meaningless pseudo-text. If the writing is small, it may be beautiful partially unreadable Russian handwriting, but not gibberish.

NON_PHYSICAL_SCREEN_TEXT_CHECK =

no captions, no subtitles, no overlay text, no floating text, no explanatory labels; any text must be physically printed or written on a real paper, label, medical form or object inside the room.

IMAGE_TEXT_REPETITION_CHECK_NEAREST_10_SCENES =

do not repeat the same visible Russian medical note, label, date, signature or document text from the nearest 10 scenes unless it is the same physical medical form continuing by direct source logic.

CONTEXT_SPECIFIC_NEGATIVES =

caricature face, exaggerated madness, comic parody, modern psychiatric ward, clean white hospital, meaningless medical pseudo-text, unrelated wall labels, wrong-language medical text, non-physical captions, repeated document text, repeated poster text, copper basin, brass basin, metal bowl, washstand, washbasin, large foreground prop.

ПРИМЕР ГОТОВОГО PROMPT_1:

Create one unified scene, not a collage, not multiple visual panels inside one image, in a trash polka + noir dark comic book + graphic novel style.

Show Friedrich Nietzsche sitting alone in a late-19th-century psychiatric clinic room, physically fragile, withdrawn, with a remote tired stare, iconic large mustache, thinning hair, pale skin and dark worn period clothing, in a modest German psychiatric ward interior with a narrow iron bed, simple wooden chair, cracked plaster walls, closed wooden door and tall window, during late 1880s or early 1890s, seated on a simple wooden chair, slightly hunched, hands resting in his lap or loosely gripping the chair, silent and mentally distant, with an archival crime-thriller mood of control, silence, breakdown and disputed legacy. The scene must feel historically grounded, physically believable and material, not symbolic, not theatrical, not modernized.

Context logic: the scene shows a material trace of breakdown and institutional control through posture, room details, period furniture and silence, without invented clues and without foreground clue objects. Every visible detail must come from the source frame, video text, voiceover, location, time period or stated conflict. Do not add fictional clues, new objects, extra characters, random detective props, foreground clue objects or decorative story hints. Do not create a synonym scene that repeats the same meaning, action, composition, mood or visual function of nearby scenes unless the source data directly requires repetition or continuity.

Synonym scene control: do not repeat a nearby scene of a lone figure in a dark institutional room unless the source requires it; if a similar scene exists nearby, distinguish this frame through Nietzsche’s specific seated posture, psychiatric clinic context, period room texture and visible institutional stillness.

Foreground control: minimal unobtrusive foreground, no large prop at the front edge, no basin, no bowl, no washstand, no document pile, no object pushed toward camera. Keep the main subject in the midground or readable central area by default. Keep the foreground minimal and unobtrusive. Do not place large props, bowls, basins, documents, furniture, symbolic objects, weapons, maps, books or clue-like items at the front edge unless explicitly required by the source data. Build spatial depth through architecture, light, posture and supported environment details, not through random foreground objects.

Repetition control: avoid repeating any distinctive object or foreground composition from the previous three frames; use posture, window light, empty wall space and institutional room texture instead. Do not repeat the same distinctive object, prop, furniture item, basin, bowl, metal vessel, document stack, lamp, table, chair, window setup or foreground composition from any of the previous three frames unless the source data explicitly requires continuity.

Composition: use one cinematic frame with a single readable focal point: Nietzsche seated alone in the room. Use controlled cinematic framing and medium-distance composition by default. Build the scene with rule of thirds, strong silhouette design, bold noir framing and readable spatial depth. The chaotic trash polka energy must be integrated into one unified poster-like composition, not split into collage panels. Board usage: no detective board, no investigation wall, no strings, no clue map; context is shown through physical environment and posture. Do not create a detective board, investigation wall, clue map, strings or red connecting lines unless the source data explicitly requires a real physical board inside the scene.

Lighting: use cold rain-washed side light from the window, pale winter sky glow, muted amber-gray interior tones, deep soft shadows and realistic chiaroscuro to separate the subject from the environment.

Style: emphasize trash polka aesthetics through distressed paper texture, halftone grain, rough print imperfections, smeared charcoal shadows, spray texture, ripped poster fragments and dirty cream paper, raw brush smears, ink splashes, spray-paint effects, analog print noise, high-contrast graphic overlays and gritty comic inking.

Red accents: use rough blood-red brush slashes, distressed print interference, torn red poster fragments and smeared red ink stress marks integrated into the room, with no circles or target marks, only as jagged red brush slashes, smeared paint, distressed print interference, torn poster fragments, red ink splashes, rough scratches and abstract stress marks integrated into the environment.

Realism texture: add aged cracked plaster, chipped paint, worn dark wood grain, thin institutional bedding, dusty corners, worn floor texture, faded fabric, paper fibers, rough ink scratches and analog print noise with tactile historical material detail. Do not create copper basins, brass basins, metal bowls, washbasins, washstands or recurring vessels.

Historical/material details: include period-appropriate wooden chair, iron bed, old institutional bedding, cracked plaster, closed wooden door, tall window, no modern medical equipment, no modern lighting, no plastic objects, no washstand, no basin. All objects must belong to the scene and time period and must be supported by source data.

Text rule: if papers, labels or medical forms are visible, they must contain coherent Russian text appropriate to the object type and nearby context; medical papers must look like Russian medical notes, with no isolated words, no random phrases and no meaningless pseudo-text. If the writing is small, it may be beautiful partially unreadable Russian handwriting, but not gibberish. Non-physical screen text check: no captions, no subtitles, no overlay text, no floating text, no explanatory labels; any text must be physically printed or written on a real paper, label, medical form or object inside the room. Image text repetition check: do not repeat the same visible Russian medical note, label, date, signature or document text from the nearest 10 scenes unless it is the same physical medical form continuing by direct source logic.

Final style lock: unified cinematic frame, trash polka, noir comic, graphic novel, grunge poster art, distressed printmaking, realistic historical texture, high-contrast mixed media illustration, gritty comic inking, rough contour lines, heavy shadow masses, halftone grain, dirty paper surface, raw brush smears, ink splashes, spray-paint distress, black, off-white, dirty cream, charcoal, dark gray, muted amber-gray and vivid blood-red slashes only, no red circles, no isolated words, no random word pairs, no meaningless pseudo-text, no Latin filler, contextual Russian text only on source-supported papers/notes/posters, no non-physical screen text, no captions, no subtitles, no overlay text, no repeated image text across nearest 10 scenes, no automatic detective board, no invented details, no large foreground props, no copper basin, no brass basin, no washstand, no repeated distinctive prop from the previous three frames, no synonym scene without direct need.

ПРИМЕР ГОТОВОГО NEGATIVE PROMPT_1:

isolated single words, random word pairs, disconnected short phrases, repeated image text across nearest ten scenes, repeated paper text, repeated poster text, repeated diary text, repeated medical record text, repeated sign text, meaningless pseudo-text, fake pseudo-Cyrillic, Latin filler text, lorem ipsum, nonsensical handwriting, unrelated readable text, wrong-language text, captions, subtitles, overlay text, floating text, text across the image, graphic labels, explanatory labels, author comments, non-physical screen text, text not attached to a real object, logo, watermark, collage panels, multiple visual panels inside one image, synonym scene without direct source need, repeated scene meaning, reworded duplicate scene, same visual function as nearby scenes, evidence circles, red circles, target rings, circular highlight marks, red outlines around clues, red arrows pointing at evidence, literal evidence-board strings, automatic detective board, investigation wall, clue map, board with strings, board with red lines, invented clues, extra documents, new characters, random detective props, clean minimalist style, photorealism, glossy 3D render, cute style, pastel palette, bright cheerful colors, low detail, blurry, gore, explicit violence, modern objects, modern clothing, modern hospital equipment, fluorescent lighting, contemporary furniture, clean hospital look, surreal symbols, fantasy imagery, large foreground objects, oversized props at the front edge, repeated foreground prop, repeated clue object, foreground basin, copper basin, brass basin, metal bowl, washbasin, washstand, recurring washstand, recurring metal vessel, recurring bowl, random table objects, object pushed into camera, prop-centered composition, repeated furniture setup, same object repeated across consecutive frames, same foreground composition repeated within three frames, caricature face, exaggerated madness, comic parody, modern psychiatric ward, clean white hospital, meaningless medical pseudo-text, unrelated wall labels, wrong-language medical text

