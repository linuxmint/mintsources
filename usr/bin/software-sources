#!/usr/bin/python3

import os
import subprocess
import sys

if os.getuid() != 0:
    subprocess.run(["pkexec", "/usr/bin/mintsources"])
else:
    if len(sys.argv) > 1:
        args = sys.argv[1:]
    else:
        args = []
    exit(subprocess.run(["/usr/lib/linuxmint/mintSources/mintSources.py"] + args).returncode)
