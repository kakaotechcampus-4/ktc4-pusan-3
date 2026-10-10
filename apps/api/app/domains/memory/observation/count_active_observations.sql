SELECT
    count(*) AS total_count,
    count(*) FILTER (
        WHERE observed_range && daterange(:period_start, :period_end, '[)')
    ) AS period_count
FROM (
    SELECT observed_range
    FROM observation_food
    WHERE child_id = :child_id AND status = ANY(CAST(:statuses AS text[]))

    UNION ALL

    SELECT observed_range
    FROM observation_health
    WHERE child_id = :child_id AND status = ANY(CAST(:statuses AS text[])) AND NOT CAST(:exclude_health AS boolean)

    UNION ALL

    SELECT observed_range
    FROM observation_education
    WHERE child_id = :child_id AND status = ANY(CAST(:statuses AS text[]))

    UNION ALL

    SELECT observed_range
    FROM observation_activity
    WHERE child_id = :child_id AND status = ANY(CAST(:statuses AS text[]))

    UNION ALL

    SELECT observed_range
    FROM observation_routine
    WHERE child_id = :child_id AND status = ANY(CAST(:statuses AS text[]))
) AS active_observations
