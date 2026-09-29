import json
import time
from pathlib import Path

# region agent log
def _agent_log(hypothesis_id, location, message, data):
    try:
        import time
        line = json.dumps({
            "sessionId": "3e70c2",
            "hypothesisId": hypothesis_id,
            "location": location,
            "message": message,
            "data": data,
            "timestamp": int(time.time() * 1000),
        })
        with open(Path(__file__).with_name("debug-3e70c2.log"), "a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except OSError:
        pass
# endregion


# region agent log
def _session_dbg(hypothesis_id, location, message, data):
    try:
        with open(Path(__file__).with_name("debug-baff6e.log"), "a", encoding="utf-8") as handle:
            handle.write(json.dumps({
                "sessionId": "baff6e",
                "runId": "pre-fix",
                "hypothesisId": hypothesis_id,
                "location": location,
                "message": message,
                "data": data,
                "timestamp": int(time.time() * 1000),
            }) + "\n")
    except OSError:
        pass
# endregion


# region agent log
def _dbg(hypothesis_id, location, message, data):
    try:
        with open(Path(__file__).with_name("debug-1e58a2.log"), "a", encoding="utf-8") as handle:
            handle.write(json.dumps({
                "sessionId": "1e58a2",
                "hypothesisId": hypothesis_id,
                "location": location,
                "message": message,
                "data": data,
                "timestamp": int(time.time() * 1000),
            }) + "\n")
    except OSError:
        pass
# endregion
