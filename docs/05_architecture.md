# 05. Архитектура приложения

## 1. Архитектурные решения (ADR)

| ID | Решение | Почему | Чем платим |
|---|---|---|---|
| ADR-01 | Слои `raw → core → calc → audit` в одной БД | Ошибочные данные не ломают расчёты и не теряются; легко объяснить на собеседовании | Дублирование структур `raw` и `core` |
| ADR-02 | Агрегаты по продажам считаются в SQL, расчёт цены по предложению — в Python на `Decimal` | SQL быстро и точно (`NUMERIC`) суммирует 400 тыс. строк; алгоритм цены с ветвлениями проще читать и тестировать в Python | Логика метрик есть в SQL, поэтому нужен тест согласованности (TC-15) |
| ADR-03 | Драйвер `psycopg` 3 без ORM | Меньше «магии», автор видит настоящий SQL; одна зависимость вместо SQLAlchemy + Alembic | Миграции пишем сами |
| ADR-04 | Миграции — пронумерованные `.sql` файлы и раннер на ~60 строк с таблицей `audit.schema_migration` и контрольной суммой | ТЗ допускает «строго управляемую инициализацию»; понятно без изучения Alembic | Нет автогенерации и отката (для прототипа не нужно) |
| ADR-05 | Модули расчёта не импортируют Streamlit | Тестируются Pytest без интерфейса (требование ТЗ §28) | — |
| ADR-06 | Зависимости через `uv` и `uv.lock` | Точная фиксация версий в связке с `pyproject.toml`, быстрая установка в Docker и CI | Ещё один инструмент; альтернатива — `pip-tools` |
| ADR-07 | Интеграционные тесты против реального PostgreSQL (сервис в Compose и в GitHub Actions) | SQLite не поддерживает `NUMERIC`, частичные индексы и `ON CONFLICT` так же | Тесты медленнее модульных; помечены `@pytest.mark.integration` |
| ADR-08 | INFO-события в журнал, а не в таблицу алертов | 2 250 «цена рассчитана» в списке предупреждений сделали бы его бесполезным | Два места для событий |
| ADR-09 | Расчёт запуска — одна транзакция | Нет «половинчатых» запусков при сбое | Блокировка на время расчёта (секунды) |
| ADR-10 | Роли — переключатель в верхней строке страницы (боковая панель — только разделы) | ТЗ разрешает; честная пометка «не защита» | Нет реального контроля доступа |
| ADR-11 | `openpyxl` для XLSX | Нужен Pandas для записи Excel; это единственная дополнительная библиотека вне стека ТЗ | +1 зависимость |

## 2. Контекстная диаграмма

```mermaid
flowchart LR
    analyst([Аналитик])
    head([Руководитель сети])
    opman([Операционный менеджер])

    subgraph sources [Источники данных]
        gen[Генератор демо-данных<br/>в реальности: касса, учётная система, техкарты]
    end

    app[[Система контроля цен<br/>и маржинальности]]

    subgraph outputs [Результаты]
        dash[Показатели и графики]
        recs[Рекомендуемые цены<br/>с объяснением]
        alerts[Предупреждения]
        files[Выгрузки CSV / Excel]
    end

    pos[(Кассовая система<br/>цены НЕ меняются)]

    gen -->|продажи, цены, себестоимость,<br/>остатки, списания, промо| app
    analyst -->|правила, запуск расчёта| app
    head -->|согласование| app
    opman -->|просмотр своей точки| app
    app --> dash & recs & alerts & files
    app -.->|нет интеграции в v1| pos
```

**Простыми словами:** три типа пользователей работают с одним приложением. Данные приходят из генератора (в реальной жизни это была бы касса и учётная система). На выходе — показатели, рекомендации, предупреждения и файлы. Пунктир к кассе подчёркивает: система ничего в кассу не отправляет.

## 3. Компонентная архитектура

```mermaid
flowchart TB
    subgraph ui [Веб-интерфейс · Streamlit]
        p1[Обзор]
        p2[Анализ цен]
        p3[Предупреждения]
        p4[Сценарии]
        p5[История]
        p6[Экспорт]
        role[Переключатель роли]
    end

    subgraph core_py [Python-пакет pmc]
        repo[repository<br/>SQL-запросы, чтение/запись]
        metrics[metrics<br/>М-01…М-07]
        pricing[pricing<br/>алгоритм цены, Decimal]
        rules[rules<br/>выбор правила, версии]
        alerts_m[alerts<br/>правила алертов, dedup]
        scen[scenarios<br/>до/после, допущения]
        exp[export<br/>CSV, XLSX]
        quality[quality<br/>проверки качества]
        runsvc[run_service<br/>оркестрация запуска]
        log[logging_setup]
    end

    subgraph tools [Служебные команды]
        gen[generator<br/>seed, манифест ошибок]
        loader[loader<br/>raw → core]
        mig[migrate<br/>SQL-миграции]
    end

    db[(PostgreSQL<br/>raw · core · calc · audit)]
    journal[(audit.event_log<br/>журнал событий)]

    subgraph tests [Автоматические тесты]
        unit[unit]
        integ[integration]
        uitest[ui · AppTest]
    end

    ci[GitHub Actions<br/>ruff + pytest]

    ui --> runsvc & metrics & scen & exp & repo
    runsvc --> rules & pricing & alerts_m & repo
    metrics --> repo
    scen --> pricing
    repo --> db
    runsvc --> journal
    gen --> loader --> quality --> repo
    mig --> db
    journal --- db
    unit --> pricing & metrics & quality & scen
    integ --> repo & runsvc & loader
    uitest --> ui
    ci --> tests
```

**Простыми словами:** интерфейс только показывает данные и передаёт команды. Вся логика — в пакете `pmc` (от «price margin control»), разбитом на небольшие модули с одной задачей каждый. Только модуль `repository` знает про SQL. Генератор, загрузчик и миграции — отдельные команды, которые запускаются при старте контейнера. Тесты проверяют модули по отдельности и вместе с базой; GitHub Actions запускает их при каждом изменении.

## 4. Бизнес-процесс «от данных до решения по цене»

```mermaid
flowchart TD
    start([Начало: новые данные]) --> load[Загрузить в raw]
    load --> dq{Проверки качества<br/>пройдены?}
    dq -->|строка с ошибкой| dqlog[Записать в audit.dq_issue<br/>+ алерт INVALID_FIN_DATA]
    dq -->|строка валидна| core[Перенести в core]
    dqlog --> core
    core --> key{Запуск с таким ключом<br/>уже есть?}
    key -->|да, без принуждения| show[Показать существующий запуск]
    key -->|нет или принудительно| rule{Есть глобальное<br/>правило?}
    rule -->|нет| fail[Запуск failed<br/>сообщение аналитику]
    rule -->|да| loop[Для каждого активного предложения]
    loop --> valid{Данные валидны?}
    valid -->|нет| blocked[BLOCKED + алерт CRIT]
    valid -->|да| calc[Базовая цена → округление → мин. маржа]
    calc --> limit{В пределах лимита<br/>изменения?}
    limit -->|нет| clamp[Ограничить лимитом]
    clamp --> recheck{Мин. маржа<br/>соблюдена?}
    recheck -->|нет| conflict[CONFLICT + алерт RULE_CONFLICT]
    recheck -->|да| promo
    limit -->|да| promo{Товар в промо?}
    promo -->|да| hold[ON_HOLD]
    promo -->|нет| same{Цена изменилась?}
    same -->|нет| nochange[NO_CHANGE]
    same -->|да| proposed[PROPOSED]
    blocked & conflict & hold & nochange & proposed --> save[Сохранить в одной транзакции<br/>+ событие RUN_COMPLETED]
    save --> review{Решение руководителя}
    review -->|согласовать| approved[APPROVED]
    review -->|отклонить| rejected[REJECTED]
    review -->|CONFLICT: ввести цену вручную| approved
    approved & rejected --> journal[Запись в журнал]
    journal --> finish([Конец: цены в кассе меняются вне системы])
    show --> finish
    fail --> finish
```

**Простыми словами:** сначала очищаем данные, потом для каждой пары «точка × товар» проходим по правилам. Каждая «развилка» — это бизнес-правило из документа 02. Любой исход сохраняется с причиной, поэтому всегда понятно, почему система предложила именно это. Последний шаг — человек, а не система.

## 5. Последовательность: запуск пересчёта цен (UC-01)

```mermaid
sequenceDiagram
    autonumber
    actor A as Аналитик
    participant UI as Streamlit · «Анализ цен»
    participant RS as run_service
    participant RL as rules
    participant PR as pricing
    participant AL as alerts
    participant DB as PostgreSQL

    A->>UI: Дата 2026-03-31, «Рассчитать»
    UI->>RS: start_run: as_of, force=False
    RS->>DB: отпечаток данных и активная версия правил
    DB-->>RS: fingerprint, rule_set v3
    RS->>DB: SELECT run WHERE idempotency_key = …
    alt запуск уже есть
        DB-->>RS: run_id
        RS-->>UI: существующий запуск
        UI-->>A: «Расчёт на эти данные уже выполнен» + кнопка «Пересчитать принудительно»
    else новый запуск
        RS->>DB: BEGIN → INSERT pricing_run, статус running
        RS->>DB: предложения на дату: цены, себестоимость, промо
        DB-->>RS: 2 250 строк
        RS->>RL: выбрать правило для каждого предложения
        RL-->>RS: правило + приоритет
        loop каждое предложение
            RS->>PR: recommend: cost, price, rule, promo
            PR-->>RS: статус, цена, причина, детали
        end
        RS->>AL: собрать алерты по результатам
        AL-->>RS: алерты с dedup_key
        RS->>DB: INSERT рекомендации
        RS->>DB: INSERT алерты ON CONFLICT по dedup_key DO UPDATE
        RS->>DB: UPDATE run = done → INSERT event_log RUN_COMPLETED → COMMIT
        RS-->>UI: сводка по статусам
        UI-->>A: Предложено 412 · без изменений 1 690 · конфликт 27 · блок 15 · промо 106
    end
    opt ошибка на любом шаге
        RS->>DB: ROLLBACK → run = failed в отдельной транзакции
        RS-->>UI: текст ошибки
    end
```

Цифры в последнем сообщении — иллюстрация формата, а не результат расчёта.

**Простыми словами:** интерфейс не считает сам — он вызывает сервис. Сервис сначала проверяет, не делали ли уже такой расчёт. Потом в одной транзакции считает все цены, записывает результаты и предупреждения. Если что-то сломалось — всё откатывается, «половины расчёта» в базе не остаётся.

## 6. Структура репозитория

```
price-margin-control/
├── compose.yaml               # db + app (+ init-job)
├── Dockerfile
├── .env.example
├── pyproject.toml / uv.lock
├── README.md
├── migrations/                # 001_schemas.sql, 002_core.sql, 003_calc.sql, …
├── src/pmc/
│   ├── config.py  db.py  logging_setup.py
│   ├── repository/            # SQL по областям: sales.py, prices.py, runs.py, alerts.py
│   ├── sql/                   # .sql файлы метрик, читаются repository
│   ├── metrics.py  pricing.py  rules.py  alerts.py
│   ├── scenarios.py  quality.py  run_service.py  export.py
│   └── cli.py                 # migrate | generate | load | run-pricing | check-db
├── generator/                 # справочники (общие с проектом №2), факты, манифест ошибок
├── app/
│   ├── Главная.py
│   ├── pages/                 # 1_Обзор.py … 6_Экспорт.py
│   └── components/            # карточки, таблицы, тема графиков
├── tests/ unit/ integration/ ui/ conftest.py
├── docs/                      # этот комплект + отчёт о реализации
└── .github/workflows/ci.yml
```

## 7. Инфраструктура

| Требование ТЗ §6 | Как обеспечиваем |
|---|---|
| compose.yaml, Dockerfile | Сервисы `db` (postgres:16-alpine), `init` (миграции → генерация → загрузка, завершается), `app` (Streamlit, порт 8501) |
| Проверка готовности | `db`: `pg_isready`; `app` стартует после `init: service_completed_successfully`; `app` healthcheck `/_stcore/health` |
| Проверка подключения | Команда `pmc check-db` (используется в `init` и вручную) |
| Создание структуры БД | `pmc migrate` с контрольными суммами в `audit.schema_migration` |
| Демо-данные | `pmc generate --seed 20261105 && pmc load`; при повторном старте пропускается, если данные уже есть |
| Логи | stdout, уровень из `LOG_LEVEL` |
| Тесты | `docker compose run --rm app pytest`; локально `uv run pytest -m "not integration"` |
| Версии | `uv.lock`, образы с явным тегом |
| Исключения из Git | `.gitignore`: `.env`, `data/`, `__pycache__/`, `.venv/`, `*.xlsx`, `exports/` |
| CI | GitHub Actions: `ruff check`, `ruff format --check`, `pytest` с сервисом PostgreSQL |
