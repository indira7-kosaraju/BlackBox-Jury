"""Container entrypoint: ``python -m bbj_gateway``."""

import sys

from bbj_gateway.server import main

sys.exit(main())
