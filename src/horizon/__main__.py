import os
import sys

# Horizon uses no pydantic plugins; looking for them scans every installed package on each hook call.
os.environ.setdefault("PYDANTIC_DISABLE_PLUGINS", "__all__")

from horizon.cli import main  # noqa: E402

sys.exit(main())
