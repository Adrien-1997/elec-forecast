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
    case "score":
        from elec_jobs.score.run import main
    case _:
        print(f"Unknown JOB_MODULE={JOB_MODULE!r}. Must be one of: ingest, features, train, score")
        sys.exit(1)

main()
