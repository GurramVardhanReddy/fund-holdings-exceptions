-- Day-over-day checks: today's snapshot (`holdings`) against the previous one
-- (`prev_holdings`) for the same funds. Same output columns as checks.sql.

WITH cur AS (
    SELECT provider, fund_id, as_of, coalesce(isin, ticker, security_name) AS sec,
           sum(weight_pct) AS w, any_value(price) AS price
    FROM holdings WHERE is_security GROUP BY 1, 2, 3, 4
), prv AS (
    SELECT provider, fund_id, as_of AS prev_as_of, coalesce(isin, ticker, security_name) AS sec,
           sum(weight_pct) AS w, any_value(price) AS price
    FROM prev_holdings WHERE is_security GROUP BY 1, 2, 3, 4
), funds AS (  -- only compare funds present on both days
    SELECT DISTINCT c.provider, c.fund_id FROM cur c JOIN prv p USING (provider, fund_id)
), j AS (
    SELECT coalesce(c.provider, p.provider) AS provider, coalesce(c.fund_id, p.fund_id) AS fund_id,
           c.as_of, p.prev_as_of, coalesce(c.sec, p.sec) AS sec,
           coalesce(c.w, 0) AS w_now, coalesce(p.w, 0) AS w_prev, c.price AS p_now, p.price AS p_prev
    FROM cur c FULL OUTER JOIN prv p USING (provider, fund_id, sec)
    WHERE (coalesce(c.provider, p.provider), coalesce(c.fund_id, p.fund_id)) IN (SELECT (provider, fund_id) FROM funds)
)
-- DOD1: a holding's weight moved by more than 2 percentage points
SELECT 'DOD1_WEIGHT_JUMP' AS rule_id, 'MEDIUM' AS severity, provider, fund_id,
       coalesce(as_of, (SELECT max(as_of) FROM holdings)) AS as_of, sec AS security,
       round(w_now - w_prev, 3) AS value, 2.0 AS threshold,
       'Weight ' || round(w_prev, 2) || '% -> ' || round(w_now, 2) || '% since ' || coalesce(prev_as_of::VARCHAR, 'previous snapshot') AS detail
FROM j WHERE abs(w_now - w_prev) > 2 AND w_prev > 0 AND w_now > 0

UNION ALL
-- DOD2: a holding above 1% appeared or disappeared
SELECT 'DOD2_NEW_OR_GONE', 'MEDIUM', provider, fund_id,
       coalesce(as_of, (SELECT max(as_of) FROM holdings)), sec, round(w_now - w_prev, 3), 1.0,
       CASE WHEN w_prev = 0 THEN 'New holding at ' || round(w_now, 2) || '%'
            ELSE 'Holding of ' || round(w_prev, 2) || '% no longer listed' END
FROM j WHERE (w_prev = 0 AND w_now > 1) OR (w_now = 0 AND w_prev > 1)

UNION ALL
-- DOD3: price moved by more than 25% in one step: corporate action or bad price
SELECT 'DOD3_PRICE_JUMP', 'HIGH', provider, fund_id, as_of, sec, round(p_now / p_prev - 1, 4), 0.25,
       'Price ' || p_prev || ' -> ' || p_now || ': check for a split, other corporate action or a bad price'
FROM j WHERE p_now > 0 AND p_prev > 0 AND abs(p_now / p_prev - 1) > 0.25
