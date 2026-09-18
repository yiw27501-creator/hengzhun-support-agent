import argparse
import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parent / 'work' / 'agent-backend'
p = argparse.ArgumentParser()
p.add_argument('--port', type=int, default=8776)
a = p.parse_args()
subprocess.run([sys.executable, 'operations.py', '--source', 'data/operations-source/user-20260917.txt', '--db', 'data/portable.operations.db', '--quality-db', 'data/portable.quality.db'], cwd=root, check=True)
print(f'Open http://127.0.0.1:{a.port}/service', flush=True)
subprocess.run([sys.executable, 'server.py', '--desktop', '--async-jobs', '--port', str(a.port), '--db', 'data/portable.db'], cwd=root, check=True)
