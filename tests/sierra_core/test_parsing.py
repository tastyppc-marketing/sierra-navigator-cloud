import json, pytest
from sierra_core.parsing import unwrap_response, coerce_pyish_json
from sierra_core.errors import EndpointError

def _envelope(inner: dict) -> str:
    return json.dumps({"d": json.dumps(inner)})

def test_unwraps_lowercase_data_envelope():
    body = _envelope({"responseCode": 0, "data": {"page": {"id": 42}}})
    assert unwrap_response(body) == {"page": {"id": 42}}

def test_unwraps_capital_keyed_no_data():
    body = _envelope({"ResponseCode": 0, "Data": None, "Message": ""})
    assert unwrap_response(body) == {}  # responseCode stripped, no data envelope -> flat empty

def test_flat_dict_without_data_strips_responsecode():
    body = _envelope({"responseCode": 0, "contentLabelId": 19778})
    assert unwrap_response(body) == {"contentLabelId": 19778}

def test_nonzero_responsecode_raises():
    body = _envelope({"responseCode": 1, "message": "Page not found"})
    with pytest.raises(EndpointError):
        unwrap_response(body)

def test_d_can_be_object_not_string():
    body = json.dumps({"d": {"responseCode": 0, "data": [1, 2, 3]}})
    assert unwrap_response(body) == [1, 2, 3]

def test_coerce_pyish_single_quoted():
    s = "{'ResponseCode':0, 'totalRecords':2, 'rows':[{\"id\":1}]}"
    out = coerce_pyish_json(s)
    assert out["totalRecords"] == 2 and out["rows"][0]["id"] == 1

def test_unwrap_falls_back_to_pyish_coerce():
    body = json.dumps({"d": "{'responseCode':0,'contentLabelId':19778}"})
    assert unwrap_response(body) == {"contentLabelId": 19778}


# --------------------------------------------------------------------------- #
# W1-T3 (#9): ASP.NET exception envelopes raise; ordinary dicts still return
# --------------------------------------------------------------------------- #

def test_unwrap_raises_on_aspnet_exception_envelope():
    with pytest.raises(EndpointError):
        unwrap_response(_envelope({"Message": "Object reference not set", "StackTrace": "at X.Y()"}))
    with pytest.raises(EndpointError):
        unwrap_response(_envelope({"Message": "boom", "ExceptionType": "System.NullReferenceException"}))


def test_unwrap_returns_ordinary_dict_without_error_markers():
    # No responseCode and no exception markers → returned unchanged (conservative).
    assert unwrap_response(_envelope({"id": 1, "name": "ok"})) == {"id": 1, "name": "ok"}
    # 'Message' alone (no StackTrace/ExceptionType) is NOT treated as an error.
    assert unwrap_response(_envelope({"Message": "hi", "id": 2})) == {"Message": "hi", "id": 2}
