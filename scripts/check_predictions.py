from elec_jobs.shared.bq import get_client

client = get_client()
sql = """
SELECT
    DATE(forecast_horizon_dt) AS day,
    DATE(forecasted_at)       AS made_on,
    COUNT(*)                  AS n
FROM `elec-forecast.elec_ml.predictions`
WHERE forecast_horizon_dt >= TIMESTAMP('2026-03-02')
GROUP BY 1, 2
ORDER BY 1, 2
"""
for r in client.query(sql).result():
    print(r["day"], "<- made on ->", r["made_on"], f"  n={r['n']}")
