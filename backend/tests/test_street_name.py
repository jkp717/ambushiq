import asyncio

import httpx
import pytest
from fastapi import HTTPException

from app.roads import router as roads_router, street_name


def esri(address="197 Scott Ln", x=-88.657997, y=37.509984, status=200, body=None):
    def handler(request):
        if status != 200:
            return httpx.Response(status, json={})
        return httpx.Response(200, json=body if body is not None else
                              {"address": {"Address": address}, "location": {"x": x, "y": y}})
    return handler


REAL_CLIENT = httpx.AsyncClient


@pytest.fixture()
def mock(monkeypatch):
    def use(handler, real=REAL_CLIENT):
        monkeypatch.setattr(street_name.httpx, "AsyncClient",
                            lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    return use


def look(lat=37.50993, lon=-88.65806):
    return asyncio.run(street_name.lookup(lat, lon))


@pytest.mark.parametrize("address,expected", [
    ("197 Scott Ln", "Scott Ln"), ("1-199 Scott Ln", "Scott Ln"), ("12B County Road 400 N", "County Road 400 N"),
    ("Scott Ln", "Scott Ln"), ("", None), (None, None),
])
def test_house_number_is_stripped(address, expected):
    assert street_name.street_from_address({"Address": address}) == expected


def test_a_close_match_gives_the_street_name(mock):
    mock(esri())
    assert look() == "Scott Ln"


def test_a_match_too_far_away_is_a_different_road(mock):
    mock(esri(y=37.5105))                       # ~60 m north of the asked point
    assert look() is None


def test_esri_errors_and_empty_answers_are_no_name(mock):
    mock(esri(status=503))
    assert look() is None
    mock(esri(body={"error": {"code": 400, "message": "Unable to find address"}}))
    assert look() is None


def test_esri_unreachable_is_no_name(mock):
    def boom(request):
        raise httpx.ConnectTimeout("slow")
    mock(boom)
    assert look() is None


def test_endpoint_returns_the_name_and_rejects_bad_points(mock):
    mock(esri())
    assert asyncio.run(roads_router.road_name(lat=37.50993, lon=-88.65806, _=None)) == {"name": "Scott Ln"}
    with pytest.raises(HTTPException) as e:
        asyncio.run(roads_router.road_name(lat=95, lon=0, _=None))
    assert e.value.status_code == 400
