"""Installed module and method-supervisor entry points."""
import sys

from .cli import main
from .supervision import method_supervisor

if len(sys.argv) == 3 and sys.argv[1] == "--_method-supervisor":
    raise SystemExit(method_supervisor(sys.argv[2]))
raise SystemExit(main())
