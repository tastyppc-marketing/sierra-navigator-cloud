from __future__ import annotations
import ast, json, re
from typing import Any
from sierra_core.errors import EndpointError

def coerce_pyish_json(s: str) -> Any | None:
    s2 = re.sub(r"([{,]\s*)'(\w+)'(\s*:)", r'\1"\2"\3', s)
    s2 = re.sub(r":\s*'([^'\\]*(?:\\.[^'\\]*)*)'(\s*[,}])", r': "\1"\2', s2)
    try:
        return json.loads(s2)
    except Exception:
        pass
    try:
        py = (s.replace(": null", ": None").replace(":null", ":None")
                .replace(": true", ": True").replace(":true", ":True")
                .replace(": false", ": False").replace(":false", ":False"))
        return ast.literal_eval(py)
    except Exception:
        return None

def unwrap_response(text: str) -> Any:
    """Unwrap Sierra's {"d": "<stringified>"} envelope to the inner data.
    Raises EndpointError on non-zero responseCode or unparseable body."""
    try:
        outer = json.loads(text or "")
    except Exception as e:
        raise EndpointError(f"json parse outer failed: {e}", raw=(text or "")[:300])
    d = outer.get("d") if isinstance(outer, dict) else outer
    if isinstance(d, str):
        try:
            d = json.loads(d)
        except Exception:
            d = coerce_pyish_json(d)
            if d is None:
                raise EndpointError("json parse inner failed", raw=text[:300])
    if isinstance(d, dict):
        rc = d.get("responseCode", d.get("ResponseCode"))
        if rc is not None:
            if rc != 0:
                raise EndpointError(f"responseCode {rc}", raw=d)
            if "data" in d:
                return d.get("data")
            if "Data" in d and d.get("Data") is not None:
                return d.get("Data")
            return {k: v for k, v in d.items()
                    if k not in ("responseCode", "ResponseCode", "Data", "Message", "message")}
        # No responseCode contract. An ASP.NET page-method exception serializes (even
        # at HTTP 200) as {"Message": ..., "StackTrace"|"ExceptionType": ...}. Raise
        # instead of returning it as if it were valid data (#9). Conservative: only the
        # unambiguous exception envelope triggers this; ordinary data dicts return below.
        if "Message" in d and ("StackTrace" in d or "ExceptionType" in d):
            raise EndpointError(f"server error: {d.get('Message')}", raw=d)
    return d
