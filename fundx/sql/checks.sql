-- Exception checks over one snapshot of the standard `holdings` table.
-- Every check returns the same columns so the results can be stacked:
--   rule_id, severity, provider, fund_id, as_of, security, value, threshold, detail
-- `fund_limits` (from config/fund_limits.csv, joined in Python) says whether a fund
-- tracks an index; ETFs here all do, which changes the UCITS concentration limits.

-- DQ1: weights should add up to ~100% of the fund
SELECT 'DQ1_WEIGHT_TOTAL' AS rule_id, 'MEDIUM' AS severity, provider, fund_id, as_of,
       NULL AS security, round(sum(weight_pct), 3) AS value, 100.0 AS threshold,
       'Weights add up to ' || round(sum(weight_pct), 3) || '%, more than 0.5 points from 100%' AS detail
FROM holdings
GROUP BY provider, fund_id, as_of
HAVING abs(sum(weight_pct) - 100) > 0.5

UNION ALL
-- DQ2: a security line with no identifier cannot be matched to reference data
SELECT 'DQ2_MISSING_ID', 'LOW', provider, fund_id, as_of, security_name, NULL, NULL,
       'No ISIN and no ticker'
FROM holdings
WHERE is_security AND isin IS NULL AND ticker IS NULL

UNION ALL
-- DQ3: a held security with no usable price. Some file layouts carry no price column
-- at all; only flag funds whose file does give prices for most lines.
SELECT 'DQ3_BAD_PRICE', 'HIGH', provider, fund_id, as_of,
       coalesce(isin, ticker, security_name), price, 0,
       'Price is missing, zero or negative'
FROM (
    SELECT *, avg(CASE WHEN price IS NOT NULL THEN 1 ELSE 0 END)
                OVER (PARTITION BY provider, fund_id, as_of) AS priced_share
    FROM holdings WHERE is_security
)
WHERE priced_share >= 0.5 AND weight_pct > 0 AND (price IS NULL OR price <= 0)

UNION ALL
-- DQ4: negative weight on a physical security (allowed for FX/derivatives, not for shares or bonds)
SELECT 'DQ4_NEGATIVE_WEIGHT', 'MEDIUM', provider, fund_id, as_of,
       coalesce(isin, ticker, security_name), weight_pct, 0,
       'Negative weight on a ' || asset_class || ' line'
FROM holdings
WHERE asset_class IN ('Equity', 'Fixed Income') AND weight_pct < 0

UNION ALL
-- DQ5: market value should equal shares x price x FX. FX is not in the files, so the
-- implied rate (value / (shares x price)) is compared with the median rate of the
-- other lines in the same fund and currency. A line far from its peers is wrong
-- somewhere: price, shares or value.
SELECT 'DQ5_VALUE_MISMATCH', 'MEDIUM', provider, fund_id, as_of,
       coalesce(isin, ticker, security_name), round(ratio / med - 1, 4), 0.05,
       'Value / (shares x price) is ' || round(100 * (ratio / med - 1), 1)
         || '% away from the other ' || currency || ' lines in this fund'
FROM (
    SELECT *, median(ratio) OVER (PARTITION BY provider, fund_id, as_of, currency) AS med,
              count(*)      OVER (PARTITION BY provider, fund_id, as_of, currency) AS n
    FROM (
        SELECT *, market_value / (shares * price) AS ratio
        FROM holdings
        WHERE asset_class = 'Equity' AND shares > 0 AND price > 0 AND market_value > 0
    )
)
WHERE n >= 5 AND abs(ratio / med - 1) > 0.05

UNION ALL
-- DQ6: the same security listed twice in one fund
SELECT 'DQ6_DUPLICATE_LINE', 'LOW', provider, fund_id, as_of, isin, count(*), 1,
       'Listed ' || count(*) || ' times'
FROM holdings
WHERE isin IS NOT NULL AND is_security
GROUP BY provider, fund_id, as_of, isin
HAVING count(*) > 1

UNION ALL
-- REC1: the same ISIN on the same day in the same currency should carry the same
-- price in every fund of the same provider
SELECT 'REC1_PRICE_DISAGREES', 'MEDIUM', provider, fund_id, as_of, isin,
       round(price / med - 1, 4), 0.01,
       'Price ' || price || ' vs ' || round(med, 4) || ' median across ' || n || ' funds'
FROM (
    SELECT *, median(price) OVER w AS med, count(*) OVER w AS n
    FROM holdings
    WHERE asset_class = 'Equity' AND isin IS NOT NULL AND price > 0
    WINDOW w AS (PARTITION BY provider, as_of, isin, currency)
)
WHERE n >= 3 AND abs(price / med - 1) > 0.01

UNION ALL
-- LIM1: single-issuer concentration in equity funds (UCITS Directive Art. 52 / 53).
-- Standard UCITS: max 10% per issuer. Index-tracking UCITS: up to 20%, and up to 35%
-- for one issuer only in exceptional market conditions. These are screening flags
-- against the public rules, not compliance findings: the prospectus decides.
SELECT 'LIM1_ISSUER_OVER_' || CASE WHEN w > 35 THEN '35' WHEN w > 20 THEN '20' ELSE '10' END,
       CASE WHEN NOT index_tracking AND w > 10 THEN 'HIGH'
            WHEN w > 35 THEN 'HIGH'
            WHEN w > 20 THEN 'HIGH'
            ELSE 'INFO' END,
       provider, fund_id, as_of, issuer, round(w, 3),
       CASE WHEN index_tracking THEN 20.0 ELSE 10.0 END,
       CASE WHEN NOT index_tracking THEN 'Issuer is ' || round(w, 2) || '% of the fund; standard UCITS limit is 10%'
            WHEN w > 35 THEN 'Issuer is ' || round(w, 2) || '% of the fund; above even the 35% exceptional index limit'
            WHEN w > 20 THEN 'Issuer is ' || round(w, 2) || '% of the fund; above the 20% index limit, so it relies on the 35% exceptional-conditions provision: escalate for prospectus check'
            ELSE 'Issuer is ' || round(w, 2) || '% of the fund; above 10%, allowed only because the fund tracks an index' END
FROM (
    SELECT h.provider, h.fund_id, h.as_of,
           h.issuer_key || ' (' || string_agg(DISTINCT h.security_name, ' + ' ORDER BY h.security_name) || ')' AS issuer,
           sum(h.weight_pct) AS w, any_value(l.index_tracking) AS index_tracking
    FROM holdings h JOIN fund_limits l USING (provider, fund_id)
    WHERE h.fund_type = 'equity' AND h.asset_class = 'Equity' AND h.issuer_key IS NOT NULL
    GROUP BY h.provider, h.fund_id, h.as_of, h.issuer_key
)
WHERE w > 10

UNION ALL
-- LIM2: the 5/40 rule. Issuers above 5% must add up to no more than 40% of a standard
-- UCITS. Index-tracking funds use the 20/35 derogation instead, so for them this is INFO.
SELECT 'LIM2_5_40_RULE',
       CASE WHEN index_tracking THEN 'INFO' ELSE 'HIGH' END,
       provider, fund_id, as_of, n_over_5 || ' issuers above 5%', round(total_over_5, 3), 40.0,
       'Issuers above 5% add up to ' || round(total_over_5, 2) || '% (standard limit 40%)'
         || CASE WHEN index_tracking THEN '; fund tracks an index, so the 20/35 derogation applies' ELSE '' END
FROM (
    SELECT provider, fund_id, as_of, any_value(index_tracking) AS index_tracking,
           count(*) AS n_over_5, sum(w) AS total_over_5
    FROM (
        SELECT h.provider, h.fund_id, h.as_of, h.issuer_key, sum(h.weight_pct) AS w,
               any_value(l.index_tracking) AS index_tracking
        FROM holdings h JOIN fund_limits l USING (provider, fund_id)
        WHERE h.fund_type = 'equity' AND h.asset_class = 'Equity' AND h.issuer_key IS NOT NULL
        GROUP BY 1, 2, 3, 4
    )
    WHERE w > 5
    GROUP BY provider, fund_id, as_of
)
WHERE total_over_5 > 40
