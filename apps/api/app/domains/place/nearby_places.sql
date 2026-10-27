-- 반경 안의 장소를 가까운 순으로 (Activity D9 · #516). 기준점은 휴대폰이 약 1km 로 흐린 좌표다.
-- 위경도 상자로 먼저 좁히고 haversine 으로 잰다. 상자는 원을 다 덮게 호출부(`bounding_box`)가 잡는다.
-- 인덱스 없이 실측 5~6ms 라 아직 두지 않았다 — 실제 데이터로 EXPLAIN 을 본 뒤 단다.
-- 같은 거리면 이름 · source_id 순이다. 결과가 매번 같아야 run 을 다시 돌려도 같은 곳이 나온다.
-- 놀이터(playground)로 찾으면 놀이터가 있는 공원(has_playground)도 온다. 그 행의 category 는 park 다.
WITH origin AS (
    SELECT
        CAST(:lat AS double precision) AS lat,
        CAST(:lng AS double precision) AS lng,
        CAST(:category AS text) AS category
), near AS (
    SELECT
        p.name,
        p.category,
        p.source,
        p.source_id,
        p.data_date,
        2 * CAST(:earth_radius_m AS double precision) * asin(least(1.0, sqrt(
            power(sin(radians(p.lat - o.lat) / 2), 2)
            + cos(radians(o.lat)) * cos(radians(p.lat))
              * power(sin(radians(p.lng - o.lng) / 2), 2)
        ))) AS distance_m
    FROM place AS p
    CROSS JOIN origin AS o
    WHERE (p.category = o.category OR (o.category = 'playground' AND p.has_playground))
      AND p.lat BETWEEN CAST(:lat_min AS double precision) AND CAST(:lat_max AS double precision)
      AND p.lng BETWEEN CAST(:lng_min AS double precision) AND CAST(:lng_max AS double precision)
)
SELECT name, category, source, data_date, distance_m
FROM near
WHERE distance_m <= CAST(:radius_m AS double precision)
ORDER BY distance_m, name, source_id
LIMIT CAST(:limit AS integer)
