WITH observations AS (
    SELECT 'observation_food' AS kind, id, child_id, status, observed_range, affinity_id FROM observation_food
    UNION ALL
    SELECT 'observation_health', id, child_id, status, observed_range, NULL::uuid FROM observation_health
    UNION ALL
    SELECT 'observation_education', id, child_id, status, observed_range, affinity_id FROM observation_education
    UNION ALL
    SELECT 'observation_activity', id, child_id, status, observed_range, affinity_id FROM observation_activity
    UNION ALL
    SELECT 'observation_routine', id, child_id, status, observed_range, affinity_id FROM observation_routine
), filtered AS (
    SELECT kind, id, observed_range
    FROM observations AS o
    WHERE child_id = :child_id
      AND status = :status
      AND (CAST(:domain AS text) IS NULL OR kind = 'observation_' || CAST(:domain AS text))
      AND (CAST(:date_from AS date) IS NULL OR observed_range && daterange(CAST(:date_from AS date), NULL, '[)'))
      AND (CAST(:date_to_exclusive AS date) IS NULL OR observed_range && daterange(NULL, CAST(:date_to_exclusive AS date), '[)'))
      AND (CAST(:affinity_id AS uuid) IS NULL OR affinity_id = CAST(:affinity_id AS uuid))
      AND (NOT :unused_in_suggestions OR NOT EXISTS (
          SELECT 1 FROM suggestion AS s
          WHERE s.child_id = :child_id
            AND s.source_refs @> jsonb_build_array(jsonb_build_object('kind', o.kind, 'id', o.id::text))
      ))
)
SELECT totals.total, page.kind, page.id, page.observed_to_exclusive
FROM (SELECT count(*) AS total FROM filtered) AS totals
LEFT JOIN LATERAL (
    SELECT kind, id, upper(observed_range) AS observed_to_exclusive
    FROM filtered
    WHERE CAST(:cursor_upper AS date) IS NULL
       OR (upper(observed_range), kind, id) < (
           CAST(:cursor_upper AS date), CAST(:cursor_kind AS text), CAST(:cursor_id AS uuid)
       )
    ORDER BY upper(observed_range) DESC, kind DESC, id DESC
    LIMIT :fetch_limit
) AS page ON true
ORDER BY page.observed_to_exclusive DESC, page.kind DESC, page.id DESC
