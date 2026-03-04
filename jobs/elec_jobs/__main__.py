"""Docker entrypoint — selects job module from JOB_MODULE env var."""

import os
import sys

JOB_MODULE = os.environ.get("JOB_MODULE", "")

match JOB_MODULE:
    case "ingest":
        from elec_jobs.ingest.run import main
    case "features":
        from elec_jobs.features.run import main
    case "train":
        from elec_jobs.train.run import main
    case "forecast":
        from elec_jobs.forecast.run import main
    case "metrics":
        from elec_jobs.metrics.run import main
    case "backfill":
        from elec_jobs.backfill.run import main
    case _:
        print(f"Unknown JOB_MODULE={JOB_MODULE!r}. Must be one of: ingest, features, train, forecast, metrics, backfill")
        sys.exit(1)

main()
