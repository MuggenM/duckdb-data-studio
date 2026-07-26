#!/bin/bash

# Apply Python 3.13 serialization bug patch to dbt-power-user extension if installed
BRIDGE_FILE="/home/coder/.local/share/code-server/extensions/innoverio.vscode-dbt-power-user-0.64.0-linux-x64/dist/node_python_bridge.py"
if [ -f "$BRIDGE_FILE" ]; then
    python3 -c "
path = '$BRIDGE_FILE'
with open(path, 'r') as f:
    code = f.read()
target = 'math.isnan(o)'
if target in code and 'isinstance' not in code:
    print('Applying math.isnan patch to dbt-power-user...', flush=True)
    code = code.replace(
        '        if math.isnan(o):\n            return \'NaN\'\n        if math.isinf(o):\n            return \'Infinity\' if o > 0 else \'-Infinity\'',
        '        try:\n            if isinstance(o, (int, float)):\n                if math.isnan(o):\n                    return \'NaN\'\n                if math.isinf(o):\n                    return \'Infinity\' if o > 0 else \'-Infinity\'\n        except:\n            pass'
    )
    with open(path, 'w') as f:
        f.write(code)
"
fi

# Execute the default code-server entrypoint
exec dumb-init /usr/bin/code-server "$@"
