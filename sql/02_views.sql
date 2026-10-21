CREATE OR REPLACE VIEW analysis.v_site_ranked AS
SELECT s.*,
       round((s.area_m2 / 10000.0)::numeric, 2) AS area_ha,
       CASE WHEN s.in_urban_plan
            THEN rank() OVER (PARTITION BY s.run_id, s.in_urban_plan ORDER BY s.area_m2 DESC)
       END AS rank_by_area
FROM analysis.site s;

CREATE OR REPLACE VIEW analysis.v_parish_summary AS
SELECT run_id,
       COALESCE(parish_name, '(unassigned)')        AS parish_name,
       count(*)                                      AS n_sites,
       round((sum(area_m2) / 10000.0)::numeric, 2)   AS total_ha,
       round((max(area_m2) / 10000.0)::numeric, 2)   AS largest_ha
FROM analysis.site
WHERE in_urban_plan
GROUP BY run_id, COALESCE(parish_name, '(unassigned)');
