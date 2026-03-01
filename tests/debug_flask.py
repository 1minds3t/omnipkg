"""
Run this directly on Windows:
  python debug_flask.py

It replicates exactly what FlaskAppManager.start() does and writes
all output to debug_flask_output.txt so we can see the crash reason.
"""
import subprocess, sys, tempfile, time, os
from pathlib import Path

OUT = Path(__file__).parent / "debug_flask_output.txt"

# Minimal flask app - same as SIMPLE_FLASK_APP in tests
code = '''
from flask import Flask
app = Flask(__name__)

@app.route("/")
def hello():
    return "hello"

if __name__ == "__main__":
    app.run(port=5001, debug=False, use_reloader=False)
'''

with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False, encoding='utf-8') as f:
    f.write(code)
    tmp = f.name

print(f"Script written to: {tmp}")

env = os.environ.copy()
env['PYTHONIOENCODING'] = 'utf-8'
env['PYTHONUTF8'] = '1'

p = subprocess.Popen(
    [sys.executable, tmp],
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    env=env,
    encoding='utf-8',
)

time.sleep(4)
p.terminate()
out, err = p.communicate(timeout=5)

result = f"""
EXIT CODE: {p.returncode}
STDOUT:
{out}
STDERR:
{err}
"""

print(result)
OUT.write_text(result, encoding='utf-8')
print(f"Output also written to: {OUT}")
