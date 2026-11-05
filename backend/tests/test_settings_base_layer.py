import pytest
from pydantic import ValidationError
from sqlalchemy import delete
from sqlalchemy.orm import Session

import app.main  # noqa: F401  (registers every model so create_all sees all tables)
from app.core.database import Base, engine
from app.settings.models import Setting
from app.settings.router import read_settings, write_settings
from app.settings.schemas import SettingsIn


@pytest.fixture()
def db():
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        s.execute(delete(Setting).where(Setting.key == "default_base_layer"))
        s.commit()


def test_the_map_opens_on_usgs_topo_until_another_base_map_is_chosen(db):
    assert read_settings(_=None)["default_base_layer"] == "topo"
    write_settings(SettingsIn(default_base_layer="esri_topo_shaded"), _=None)
    assert read_settings(_=None)["default_base_layer"] == "esri_topo_shaded"


def test_only_layer_ids_are_accepted():
    with pytest.raises(ValidationError):
        SettingsIn(default_base_layer="<script>")
