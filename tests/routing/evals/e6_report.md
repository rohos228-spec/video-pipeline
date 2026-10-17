# E6 — маршрутизация

- запросов: 40
- accuracy: 1.000 (40/40), порог 0.90
- простых на fast: 1.000, порог 0.90
- планов на strong: 1.000, порог 1.00
- доля детерминированных классификаций: 0.800 (гипотеза ~0.80, порога нет)

Спорные строки закрыты replay-фикстурами классификатора (контур A), не живой моделью.

| id | expected | predicted | det | ok |
|---|---|---|---|---|
| e6-01 | fast | fast | 1 | 1 |
| e6-02 | fast | fast | 1 | 1 |
| e6-03 | fast | fast | 1 | 1 |
| e6-04 | fast | fast | 1 | 1 |
| e6-05 | fast | fast | 1 | 1 |
| e6-06 | fast | fast | 1 | 1 |
| e6-07 | fast | fast | 1 | 1 |
| e6-08 | fast | fast | 1 | 1 |
| e6-09 | fast | fast | 1 | 1 |
| e6-10 | fast | fast | 1 | 1 |
| e6-11 | fast | fast | 1 | 1 |
| e6-12 | fast | fast | 1 | 1 |
| e6-13 | fast | fast | 1 | 1 |
| e6-14 | fast | fast | 1 | 1 |
| e6-15 | fast | fast | 1 | 1 |
| e6-16 | fast | fast | 1 | 1 |
| e6-17 | strong | strong | 1 | 1 |
| e6-18 | strong | strong | 1 | 1 |
| e6-19 | strong | strong | 1 | 1 |
| e6-20 | strong | strong | 1 | 1 |
| e6-21 | strong | strong | 1 | 1 |
| e6-22 | strong | strong | 1 | 1 |
| e6-23 | strong | strong | 1 | 1 |
| e6-24 | strong | strong | 1 | 1 |
| e6-25 | strong | strong | 1 | 1 |
| e6-26 | strong | strong | 1 | 1 |
| e6-27 | strong | strong | 1 | 1 |
| e6-28 | strong | strong | 1 | 1 |
| e6-29 | strong | strong | 1 | 1 |
| e6-30 | strong | strong | 1 | 1 |
| e6-31 | strong | strong | 1 | 1 |
| e6-32 | strong | strong | 1 | 1 |
| e6-33 | fast | fast | 0 | 1 |
| e6-34 | fast | fast | 0 | 1 |
| e6-35 | strong | strong | 0 | 1 |
| e6-36 | strong | strong | 0 | 1 |
| e6-37 | fast | fast | 0 | 1 |
| e6-38 | strong | strong | 0 | 1 |
| e6-39 | fast | fast | 0 | 1 |
| e6-40 | strong | strong | 0 | 1 |
