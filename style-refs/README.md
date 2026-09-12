# style-refs — референсы стиля

В каждой папке лежат картинки, `manifest.csv` (файл → запрос, ссылка на источник)
и `_contact_sheet.jpg` — вся папка одним листом, чтобы смотреть без скачивания.

| Папка | Шт. | Что внутри | Источник |
|---|---|---|---|
| `animation` | 150 | кадры анимации и аниме 2022–2026: Jujutsu Kaisen, Frieren, Dandadan, Arcane, Spider-Verse, Ne Zha 2, Flow, Wallace & Gromit, Inside Out 2, Cyberpunk Edgerunners | кадры таймлайна YouTube |
| `infographic` | 68 | дата-виз, дашборды, редакционная вёрстка, постеры, плоский вектор, изометрия | are.na (блоки с 2025) + Civitai |
| `photo` | 80 | кадры кино 2022–2026 (Dune 2, Poor Things, Oppenheimer, The Substance, Anora, Nosferatu) и современная фотография / фэшн-съёмка | кадры таймлайна YouTube + are.na |
| `retro` | 160 | кадры русского кино 1991–2009 — только кадры, без афиш | кадры таймлайна YouTube |
| `misc` | 50 | всё остальное: рисограф, пластилин, пиксель-арт, бумажная аппликация, линогравюра, коллаж, чертёж | Civitai |

Пересобрать: `python scripts/fetch_style_refs.py --source <arena|civitai|kinoframes> --out <папка> --limit N -q "запрос"`.
Для are.na есть `--since YYYY-MM-DD`, иначе в выдачу лезут архивы десятилетней давности.
