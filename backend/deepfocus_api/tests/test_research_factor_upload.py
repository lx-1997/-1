import base64
import json

import pytest

from deepfocus_api.research_factors import _records


@pytest.mark.parametrize("payload", [[{"date": "2024-01-02", "symbol": "DEMO"}], {"prices": [{"date": "2024-01-02", "symbol": "DEMO"}]}])
def test_json_file_supports_array_and_prices_object(payload):
    data = {"file_name": "prices.json", "content_base64": base64.b64encode(json.dumps(payload).encode()).decode()}
    assert _records(data) == [{"date": "2024-01-02", "symbol": "DEMO"}]


@pytest.mark.parametrize("content", ["{bad}", '{"unrelated": []}', '"text"'])
def test_json_file_invalid_shapes_rejected(content):
    with pytest.raises(ValueError):
        _records({"file_name": "prices.json", "content_base64": base64.b64encode(content.encode()).decode()})
