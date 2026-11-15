#!/usr/bin/env python3
"""Entry point for the fail-closed scheduled job adapter."""
from ops.scheduled_jobs import main

raise SystemExit(main())
