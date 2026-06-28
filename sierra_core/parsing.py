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
    # A genuine ASP.NET page-method fault carries NO "d" wrapper, so `d` is None and the
    # dict-guard below never fires. Detect the fault on the TOP-LEVEL body and raise, so a
    # faulted READ never degrades to {}/[] and a faulted non-delete WRITE is never audited
    # result="ok" (#9's twin on the read/non-delete-write surface — re-audit #2 MEDIUM).
    if (
        d is None
        and isinstance(outer, dict)
        and "Message" in outer
        and ("StackTrace" in outer or "ExceptionType" in outer)
    ):
        raise EndpointError(f"server error: {outer.get('Message')}", raw=outer)
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
            # Strip the envelope bookkeeping but PRESERVE a non-empty business-rule
            # message: at responseCode==0 Sierra still reports soft failures (e.g. a
            # blocked delete) in the message body, not via the status code (#9). Empty
            # messages are dropped so a bare responseCode:0 success stays an empty dict.
            return {
                k: v for k, v in d.items()
                if k not in ("responseCode", "ResponseCode", "Data")
                and not (k in ("Message", "message") and not v)
            }
        # No responseCode contract. An ASP.NET page-method exception serializes (even
        # at HTTP 200) as {"Message": ..., "StackTrace"|"ExceptionType": ...}. Raise
        # instead of returning it as if it were valid data (#9). Conservative: only the
        # unambiguous exception envelope triggers this; ordinary data dicts return below.
        if "Message" in d and ("StackTrace" in d or "ExceptionType" in d):
            raise EndpointError(f"server error: {d.get('Message')}", raw=d)
    return d
