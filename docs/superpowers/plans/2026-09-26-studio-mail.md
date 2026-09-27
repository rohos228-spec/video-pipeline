# Почта Studio: регистрация, хранение, отправка

## Простыми словами

В Studio появится окно «Почта».

Человек вводит почту и пароль и нажимает «Зарегистрироваться». Пароль в базу в открытом виде не записывается: туда попадает только перемешанная строка, из которой пароль не прочитать. Повторно занять тот же адрес нельзя.

После регистрации на адрес уходит письмо со ссылкой «подтвердите почту». Пока ссылку не откроют, войти и писать письма нельзя. Кнопка «Ссылку ещё раз» шлёт её повторно.

Потом человек входит и видит личный кабинет: свой адрес, пометку «почта подтверждена», дату регистрации и кнопку «Выйти». Выход гасит вход и в браузере, и в базе. Ниже — форма: кому, тема, текст, кнопка «Отправить».

Письмо сначала сохраняется в списке «исходящие». Сервер отправки задаётся в `.env`: `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM`. Порт 587 — обычное шифрование, порт 465 — Яндекс и Mail.ru. Если сервер указан, статус «Отправлено». Если нет — «Сохранено», на ящик ничего не уходит.

Чужой человек свои письма в этом списке не видит. Двойной клик по «Отправить» не создаёт два одинаковых письма.

Старый вход в «Сеть» (логин и пароль флота) остаётся как был. Он почту не открывает.

Что специально не делаем: восстановление пароля, вход через Google и письма о шагах видео.

Дальше в файле — тот же план для исполнителя: файлы, код и тесты.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Человек регистрируется в Studio по email, пароль хранится только как PBKDF2-хеш, со страницы «Почта» создаёт письмо, и письмо либо уходит по SMTP, либо остаётся в исходящих со статусом. Тест доказывает это без сети.

**Architecture:** Те же FastAPI и SQLite `data/state.db`. Новые таблицы создаёт уже существующий `Base.metadata.create_all` в `app/main.py:_init_db`. Пароль не покидает `app/services/passwords.py`. Письмо сначала пишется в `mail_messages`, потом при непустом `SMTP_HOST` уходит через `smtplib`. Сессия начинает помнить, какому пользователю выдан токен: текущий словарь `_TOKENS: dict[str, float]` этого не умеет, без этой правки исходящие нельзя привязать к отправителю.

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy 2, SQLite, `hashlib.pbkdf2_hmac`, `hmac.compare_digest`, `smtplib` (стандартная библиотека), React в `web/src`. Новых пакетов нет.

## Global Constraints

- Новых зависимостей нет.
- Пароль, хеш и `SMTP_PASSWORD` не попадают в лог, в JSON ответа и в репозиторий.
- Тесты не открывают сокет и не читают `.env`.
- Пайплайн, промпты, Telegram, кадры и каталоги проектов не меняются.
- Реализация — ветка `vp/studio-mail` в `.worktrees/studio-mail`. Push и merge не делать.
- Правка `web/src` → `python scripts/bump_studio_version.py`. `web/out` руками не править.
- Коммит в `main` запрещён.
- Угроза, от которой защищаемся: локальный Studio на `127.0.0.1`, не публичный сайт. Токен в `localStorage` остаётся, как у флота (`vp_fleet_token`): CORS сейчас `allow_credentials=False`, перевод на httpOnly-cookie ломает флот. Это записано как граница, не как забытый пункт.

## Три подхода

| | A. Отдельный сервис почты | B. Один пароль в `.env` | C. Таблицы в `state.db` |
|---|---|---|---|
| Где живёт | второй процесс и вторая БД | `WEB_AUTH_PASSWORD`, уже есть | `studio_users`, `mail_messages` |
| Регистрация | своя | нет, один логин на всех | `POST /api/auth/register` |
| Письмо | своя очередь | некуда писать | строка в БД, затем SMTP |
| Цена | новый порт, два деплоя | ничего не решает | `create_all` уже вызывается при старте |

Выбран **C**. A плодит второй runtime рядом со Studio. B уже работает для флота и не умеет ни регистрацию, ни исходящие.

Флот не переезжает на новые таблицы. `WEB_AUTH_USER` / `WEB_AUTH_PASSWORD` остаются запасным входом в `app/web/routers/auth.py:login`. Почтовый пользователь — отдельная строка `StudioUser`.

## Готово

`pytest tests/test_studio_mail.py -q` зелёный: повторный email даёт 409, в БД нет открытого пароля, чужой пароль даёт 401, `POST /api/mail/send` пишет исходящее и не открывает сокет, повтор с тем же `Idempotency-Key` не создаёт вторую строку.

## Не входит

- Ссылка «подтвердите почту» входит в работу: без неё войти нельзя. Если SMTP не задан, письмо остаётся в исходящих и на ящик не уходит.
- Сброс пароля, OAuth, роли, чужие проекты.
- Замена флотового логина.
- Письма о шагах пайплайна и HITL.
- Публичная регистрация в интернет.

## Откат

Удалить worktree `.worktrees/studio-mail` и ветку `vp/studio-mail`. Новые таблицы в локальной `data/state.db` пайплайн не читает. Пока ветку не вольют, `housepc` не меняется.

## Стороны системы

1. **Личность.** Email нормализуется (`strip` + `lower`). Уникальность — ограничение SQLite, не проверка в Python перед вставкой.
2. **Секрет.** В строке только `password_hash`. Формат `pbkdf2_sha256$200000$<соль>$<хеш>`, соль 16 байт. Сравнение через `hmac.compare_digest`.
3. **Сессия.** Токен 32 байта `secrets.token_urlsafe`. Рядом хранятся `user_id` (у флота `None`) и `email`. Срок 7 суток, как сейчас `_TOKEN_TTL_SEC`.
4. **Письмо.** Сначала `INSERT` со статусом `queued`, `commit`, потом SMTP. Сбой SMTP ставит `failed` и текст ошибки без пароля. Повторной отправки из UI нет в этой версии: строка остаётся как факт попытки.
5. **Идемпотентность.** Заголовок `Idempotency-Key` до 80 символов. Пара `(user_id, idempotency_key)` уникальна. Второй запрос возвращает ту же строку и код 200.
6. **Интерфейс.** Лист «Почта» по тому же приёму, что флот: событие окна и панель в `web/src/app/page.tsx`. Токен почты — ключ `vp_mail_token`, флотовый `vp_fleet_token` не трогаем.
7. **Проверка.** Один файл `tests/test_studio_mail.py`, приложение собирается как в `tests/test_kie_create_api.py`: свой `FastAPI()` и `include_router`, без боевой `data/state.db`. SMTP подменён функцией, которая роняет тест, если её вызвали при пустом `SMTP_HOST`.

## Данные

`create_all` создаёт отсутствующие таблицы сам. Отдельный ALTER не нужен: таблиц ещё нет.

```python
class StudioUser(Base):
    __tablename__ = "studio_users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)


class MailMessage(Base):
    __tablename__ = "mail_messages"
    __table_args__ = (
        UniqueConstraint("user_id", "idempotency_key"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("studio_users.id"), index=True)
    to_addr: Mapped[str] = mapped_column(String(254))
    subject: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text())
    status: Mapped[str] = mapped_column(String(16), default="queued")  # queued|sent|failed
    error: Mapped[str | None] = mapped_column(String(300), nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
```

Колонки `password` нет. Тело письма — текст пользователя, не секрет входа.

## Как это будет написано

### 1. `app/services/passwords.py`

```python
import hashlib
import hmac
import secrets

_ITERS = 200_000


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _ITERS)
    return f"pbkdf2_sha256${_ITERS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iters_s, salt_hex, digest_hex = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(iters_s)
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(digest.hex(), digest_hex)
```

Итерации читаются из строки, чтобы старые хеши переживали смену `_ITERS`.

### 2. Сессия — правка `app/web/auth_sessions.py`

Словарь становится таким:

```python
@dataclass
class Session:
    issued: float
    user_id: int | None
    email: str

_TOKENS: dict[str, Session] = {}


def issue_session_token(email: str, user_id: int | None = None) -> str:
    token = secrets.token_urlsafe(32)
    _TOKENS[token] = Session(time.time(), user_id, email)
    return token


def session_from_request(request: Request) -> Session | None:
    token = _extract_bearer(request)
    rec = _TOKENS.get(token or "")
    if rec is None or time.time() - rec.issued > _TOKEN_TTL_SEC:
        return None
    return rec
```

`require_web_user` для флота остаётся проверкой «токен жив». Почта требует `session.user_id is not None`. Флотовый токен почту не открывает.

Вызов `issue_session_token(body.username)` в текущем логине получает новый аргумент `email=` и `user_id=None`. Это одна строка в `login`, поведение флота то же: токен есть, почты нет.

### 3. Регистрация — `app/web/routers/auth.py`

Рядом с существующим `LoginBody`.

```python
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class RegisterBody(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=8, max_length=200)


def _email(value: str) -> str:
    mail = value.strip().lower()
    if not _EMAIL.match(mail):
        raise HTTPException(422, "bad email")
    return mail


@router.post("/register", status_code=201)
async def register(body: RegisterBody, session: AsyncSession = Depends(get_session)) -> dict:
    email = _email(body.email)
    row = StudioUser(email=email, password_hash=hash_password(body.password))
    session.add(row)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(409, "email taken") from None
    return {"id": row.id, "email": row.email}
```

Ответ не содержит `password_hash`. Гонку двух регистраций закрывает `UNIQUE(email)`, не предварительный `SELECT`.

Логин после флотовой проверки:

```python
user = await session.scalar(select(StudioUser).where(StudioUser.email == body.username.strip().lower()))
if user is None or not verify_password(body.password, user.password_hash):
    raise HTTPException(401, "invalid credentials")
token = issue_session_token(user.email, user_id=user.id)
return {"ok": True, "token": token, "auth_required": True, "email": user.email}
```

Порядок в `login`: если `web_auth_enabled` и пара совпала с `WEB_AUTH_USER` / `WEB_AUTH_PASSWORD` — старый ответ с `user_id=None`. Иначе таблица. Так флот не начинает требовать строку в `studio_users`.

### 4. Отправка — `app/services/mailer.py` и `app/web/routers/mail.py`

Настройки в `app/settings.py` по тому же приёму, что `web_auth_user`:

```python
smtp_host: str = Field("", alias="SMTP_HOST")
smtp_port: int = Field(587, alias="SMTP_PORT")
smtp_user: str = Field("", alias="SMTP_USER")
smtp_password: str = Field("", alias="SMTP_PASSWORD")
smtp_from: str = Field("", alias="SMTP_FROM")
```

Доставка:

```python
def deliver(message: MailMessage) -> None:
    if not settings.smtp_host.strip():
        return
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as smtp:
        smtp.starttls()
        if settings.smtp_user:
            smtp.login(settings.smtp_user, settings.smtp_password)
        smtp.sendmail(
            settings.smtp_from or settings.smtp_user,
            [message.to_addr],
            _rfc822(message),
        )
```

`_rfc822` собирает `Subject` в UTF-8 через `email.message.EmailMessage`. Пароль SMTP в текст письма не подставляется.

Роутер:

```python
router = APIRouter(prefix="/mail", tags=["mail"])


class SendBody(BaseModel):
    to: str = Field(min_length=3, max_length=254)
    subject: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=20_000)


@router.post("/send")
async def send_mail(
    body: SendBody,
    request: Request,
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict:
    who = session_from_request(request)
    if who is None or who.user_id is None:
        raise HTTPException(401, "login required")
    if idempotency_key:
        prev = await session.scalar(
            select(MailMessage).where(
                MailMessage.user_id == who.user_id,
                MailMessage.idempotency_key == idempotency_key[:80],
            )
        )
        if prev is not None:
            return _public(prev)
    row = MailMessage(
        user_id=who.user_id,
        to_addr=_email(body.to),
        subject=body.subject.strip(),
        body=body.body,
        status="queued",
        idempotency_key=(idempotency_key or "")[:80] or None,
    )
    session.add(row)
    await session.commit()
    try:
        await asyncio.to_thread(deliver, row)
    except Exception as exc:
        row.status = "failed"
        row.error = type(exc).__name__
        await session.commit()
        return _public(row)
    if settings.smtp_host.strip():
        row.status = "sent"
        await session.commit()
    return _public(row)
```

`_public` отдаёт `id`, `to`, `subject`, `status`, `created_at`. Поле `error` отдаёт только имя класса исключения. `GET /mail/outbox` возвращает последние 50 писем этого `user_id`, чужие строки не выбираются: в `WHERE` всегда `user_id == who.user_id`.

Подключение в `app/web/api.py` сразу после строки `app.include_router(auth_router.router, prefix=API_PREFIX)`:

```python
from app.web.routers import mail as mail_router
app.include_router(mail_router.router, prefix=API_PREFIX)
```

`deliver` вызывается после `commit`, чтобы падение SMTP не откатывало факт письма. `asyncio.to_thread` нужен потому, что `smtplib` блокирует цикл.

При пустом `SMTP_HOST` статус остаётся `queued`. Интерфейс пишет: «Сохранено. SMTP не задан, на ящик не уходило.» При `sent`: «Отправлено.» При `failed`: «Не отправлено» и имя ошибки.

### 5. Панель — `web/src/lib/mail-api.ts` и `web/src/components/mail/mail-panel.tsx`

Клиент повторяет `web/src/lib/fleet-api.ts`, другой ключ хранилища:

```typescript
const TOKEN_KEY = "vp_mail_token";

export async function registerMail(email: string, password: string) {
  return mailFetch<{ id: number; email: string }>("/api/auth/register", {
    method: "POST",
    body: JSON.stringify({ email, password }),
  });
}

export async function loginMail(email: string, password: string) {
  const res = await mailFetch<{ token?: string | null; email?: string }>("/api/auth/login", {
    method: "POST",
    body: JSON.stringify({ username: email, password }),
  });
  if (res.token) setMailToken(res.token);
  return res;
}

export async function sendMail(to: string, subject: string, body: string, key: string) {
  return mailFetch<OutboxItem>("/api/mail/send", {
    method: "POST",
    headers: { "Idempotency-Key": key },
    body: JSON.stringify({ to, subject, body }),
  });
}
```

`key` — `crypto.randomUUID()` на одно нажатие кнопки. Повтор того же нажатия (двойной клик) шлёт тот же ключ.

Панель — три блока в одном листе:

- форма регистрации, если токена нет;
- форма входа под ней;
- после входа форма «кому / тема / текст» и список `GET /api/mail/outbox`.

Открытие: в оболочке кнопка шлёт `window.dispatchEvent(new Event("studio-open-mail"))`. `web/src/app/page.tsx` слушает его так же, как `studio-open-fleet` на строках 48–49, и ставит `mailOpen`. Лист — рядом с `FleetPanelSheet`.

После правки `web/src` выполняется `python scripts/bump_studio_version.py`.

## Ошибки, которые обязан увидеть тест

| Вызов | Результат |
|---|---|
| `POST /register` нормальный | 201, в JSON нет `password` и `password_hash` |
| тот же email ещё раз | 409 |
| пароль короче 8 | 422 |
| email `not-an-email` | 422 |
| `POST /login` верный | 200 и `token` |
| `POST /login` чужой пароль | 401, тело ответа одинаковое с «нет такого email» |
| `POST /mail/send` без токена | 401 |
| `POST /mail/send` с флотовым токеном (`user_id is None`) | 401 |
| `POST /mail/send` свой токен, `SMTP_HOST=""` | 200, `status=queued`, функция `deliver` не вызывает `smtplib.SMTP` |
| тот же `Idempotency-Key` | одна строка в таблице |
| `GET /mail/outbox` другим пользователем | пусто, чужое письмо не видно |

Последний случай — два пользователя в одном тесте. Это проверка стороны «хранение»: исходящие не общие.

Каркас теста — как `tests/test_kie_create_api.py`: `_app()` собирает маленький `FastAPI`, роутеры `auth` и `mail`, сессия SQLite `:memory:`. Боевой `data/state.db` не открывается. `deliver` в тесте подменяется на функцию, которая пишет в список `calls`. При пустом хосте список пуст.

## Порядок в коде

Один исполнитель не держит все файлы сразу. Файлы задач не пересекаются, кроме последней стыковки.

- [ ] **1. Хеш.** Написать `tests/test_studio_mail.py::test_hash_is_salted_and_verifies` до реализации. Два `hash_password("same")` не равны, `verify_password` истинен только для исходной строки, подстрока `same` в хеш не входит. Затем `app/services/passwords.py` целиком, как в разделе 1. Прогон этого теста.
- [ ] **2. Таблицы и сессия.** Тест создаёт `StudioUser` дважды с одним email и ждёт `IntegrityError`. Тест `issue_session_token` / `session_from_request` отличает `user_id=None` от числа. Затем модели в `app/models.py` и замена `_TOKENS` в `app/web/auth_sessions.py`. Прогнать `tests/test_studio_mail.py` и существующие тесты, которые импортируют `issue_session_token` (поиск по репозиторию перед правкой). Сигнатура `issue_session_token(email, user_id=None)` обязана принимать старый вызов с одним позиционным аргументом: первый аргумент остаётся email/username.
- [ ] **3. Регистрация и вход.** Тесты таблицы ошибок выше, кроме почтовых. Затем `RegisterBody` и ветка в `login`. Флотовый успех покрыт отдельным тестом с `web_auth_enabled` и парой из настроек, чтобы регрессию флота поймать здесь, а не в UI.
- [ ] **4. Исходящие.** Тесты send / idempotency / чужой outbox / сокет не открыт. Затем `mailer.py`, `mail.py`, четыре поля в `settings.py`, две строки в `api.py`.
- [ ] **5. Панель.** `mail-api.ts`, `mail-panel.tsx`, событие в `page.tsx`, `bump_studio_version.py`. Браузер на `:8765` смотрит человек после перезапуска Studio: регистрация, вход, отправка, строка со статусом «Сохранено».
- [ ] **6. Вся ветка.** `pytest tests/test_studio_mail.py -q`, затем один раз `pytest tests/ -q`. Новые падения чинить. Старые красные не трогать.

Исполнитель задач 1–4 — Composer 2.5 Fast, по одной задаче, со свежим контекстом и только перечисленными файлами. Задача 5 — этот чат: она стыкует уже готовый API с оболочкой. После каждой задачи diff читает Claude Opus 5 Thinking и не правит код сам. Замечания возвращаются исполнителю той же задачи.

Координатор этого чата после каждой закрытой задачи пишет в `.superpowers/sdd/2026-09-26-studio-mail/progress.md` одну строку: номер задачи, коммит, результат теста. Отчёт раз в 5 минут берёт числа из этого файла: сколько задач закрыто, какая идёт, сколько осталось.

## Что рискует

- Правка `issue_session_token` задевает флот. Поэтому задача 2 сначала ищет все вызовы, а задача 3 держит тест старого логина.
- `create_all` на старте Studio создаст таблицы в живой `data/state.db` после обновления кода. Обратно колонки сами не исчезнут. Пайплайн эти таблицы не читает.
- Двойной клик без `Idempotency-Key` создаст два письма. Ключ обязателен в клиенте; сервер без ключа всё равно пишет строку (ручной `curl` может слать дважды). Это записано в тесте клиента, не прячется.
