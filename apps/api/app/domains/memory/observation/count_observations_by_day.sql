WITH days AS (
    SELECT CAST(d AS date) AS day
    FROM generate_series(
        CAST(:start AS date), CAST(:end_exclusive AS date) - 1, interval '1 day'
    ) AS d
), observations AS (
    SELECT 'observation_food' AS kind, observed_range FROM observation_food
    WHERE child_id = :child_id AND status = ANY(CAST(:statuses AS text[]))
    UNION ALL
    SELECT 'observation_health', observed_range FROM observation_health
    WHERE child_id = :child_id AND status = ANY(CAST(:statuses AS text[]))
    UNION ALL
    SELECT 'observation_education', observed_range FROM observation_education
    WHERE child_id = :child_id AND status = ANY(CAST(:statuses AS text[]))
    UNION ALL
    SELECT 'observation_activity', observed_range FROM observation_activity
    WHERE child_id = :child_id AND status = ANY(CAST(:statuses AS text[]))
    UNION ALL
    SELECT 'observation_routine', observed_range FROM observation_routine
    WHERE child_id = :child_id AND status = ANY(CAST(:statuses AS text[]))
)
SELECT days.day, count(*) AS observation_count
FROM days
JOIN observations AS o
  ON o.kind = ANY(CAST(:kinds AS text[]))
 AND o.observed_range && daterange(days.day, days.day + 1, '[)')
GROUP BY days.day
