"""Dry run of the supervised resume of `items` (Kie task of one item picture): see media_dry_run_base.

The node body calls the node's own paid seam, kie_http.run_generation, with the node's own file names
(predmetN_<hex>.png). The Outsee browser ladder of the real step and the GPT rewrite are not involved; the
Kie HTTP boundary is the only fake.
"""
import unittest
import uuid

import pytest

import media_dry_run_base as base
from app.bots import kie_http
from app.models import Project, ProjectStatus
from app.services import kie_catalog

pytestmark = pytest.mark.skipif(not base.HAVE_FFMPEG, reason="ffmpeg/ffprobe are required")


class ItemsDryRun(base.MediaDryRun):
    fixture_node_type = "items"
    status = ProjectStatus.generating_items
    running_key = "generating_items"
    api = "jobs"
    media_kind = "image"
    result_ext = ".png"
    key = "predmet1"

    async def native_step(self, session, project):
        out = project.data_dir / "items" / f"predmet1_{uuid.uuid4().hex[:8]}.png"
        out.parent.mkdir(parents=True, exist_ok=True)
        spec = kie_catalog.get_model("z-image")
        payload = kie_catalog.build_payload(spec, {"prompt": "красная кружка, студийный свет", "aspect_ratio": "1:1"})
        await kie_http.run_generation(spec, payload, out, timeout_s=60, project_id=project.id)

    def result_files(self):
        return (self.data / "items").glob("predmet1_*.png")

    async def change_request_inputs(self):
        async with self.business() as session:
            project = await session.get(Project, base.PID)
            project.item_descriptions = ["синяя тарелка"]
            await session.commit()


if __name__ == "__main__":
    unittest.main(verbosity=2)
