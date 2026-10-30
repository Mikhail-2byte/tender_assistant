-- Параметры :today (YYYY-MM-DD), :now_time (HH:MM), :tomorrow задаёт приложение.
-- До подтверждения часового пояса не использовать точное время для уведомлений.
-- Активные: будущие сроки сверху, затем просроченные, затем без даты.
SELECT id, number, customer, title, status, deadline_date, deadline_time
FROM tenders
WHERE status IN ('В работе', 'Податься', 'Переторжка')
ORDER BY
 CASE WHEN deadline_date IS NULL THEN 2
      WHEN deadline_date < :today OR (deadline_date = :today AND deadline_time IS NOT NULL AND deadline_time < :now_time) THEN 1
      ELSE 0 END,
 CASE WHEN deadline_date > :today OR (deadline_date = :today AND (deadline_time IS NULL OR deadline_time >= :now_time))
      THEN deadline_date || ' ' || COALESCE(deadline_time, '23:59:59') END ASC,
 CASE WHEN deadline_date < :today OR (deadline_date = :today AND deadline_time IS NOT NULL AND deadline_time < :now_time)
      THEN deadline_date || ' ' || COALESCE(deadline_time, '23:59:59') END DESC,
 id;

-- Сегодня; замена :today на :tomorrow даёт завтрашние подачи.
SELECT * FROM tenders
WHERE status IN ('В работе', 'Податься', 'Переторжка') AND deadline_date = :today
ORDER BY deadline_time IS NULL, deadline_time, id;

-- Точный номер; он не объявлен уникальным.
SELECT * FROM tenders WHERE number = :number;
