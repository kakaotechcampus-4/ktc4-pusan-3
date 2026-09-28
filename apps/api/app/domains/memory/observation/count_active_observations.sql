SELECT
    count(*) AS total_count,
    count(*) FILTER (
        WHERE observed_range && daterange(:period_start, :period_end, '[)')
    ) AS period_count
FROM (
    SELECT observed_range
    FROM observation_food
    WHERE child_id = :child_id AND status = 'active'

    UNION ALL

    SELECT observed_range
    FROM observation_health
    WHERE child_id = :child_id AND status = 'active'

    UNION ALL

    SELECT observed_range
    FROM observation_education
    WHERE child_id = :child_id AND status = 'active'

    UNION ALL

    SELECT observed_range
    FROM observation_activity
    WHERE child_id = :child_id AND status = 'active'

    UNION ALL

    SELECT observed_range
    FROM observation_routine
    WHERE child_id = :child_id AND status = 'active'
) AS active_observations
