-- Слои данных: raw (как пришло) → core (проверено) → calc (расчёты) → audit (журналы).
CREATE SCHEMA IF NOT EXISTS raw;
CREATE SCHEMA IF NOT EXISTS core;
CREATE SCHEMA IF NOT EXISTS calc;
CREATE SCHEMA IF NOT EXISTS audit;
