import copy
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from azure.core.credentials import AzureSasCredential
from azure.core.exceptions import ResourceExistsError, ResourceModifiedError
from azure.core.pipeline.transport import HttpResponse, HttpTransport
from azure.storage.blob import ContainerClient
from requests.structures import CaseInsensitiveDict

from app.blob_storage import BlobStorage


class FakeContainer:
    def __init__(self):
        self.blobs = {}
        self.version = 0
        self.fail_write = False

    def list_blobs(self, **kwargs):
        return [SimpleNamespace(name=name) for name in self.blobs]

    def download_blob(self, name):
        data, etag = self.blobs[name]
        return SimpleNamespace(readall=lambda: data, properties=SimpleNamespace(etag=etag))

    def upload_blob(self, name, data, **kwargs):
        if self.fail_write:
            raise OSError("storage unavailable")
        if name in self.blobs:
            if not kwargs["overwrite"]:
                raise ResourceExistsError()
            if kwargs["etag"] != self.blobs[name][1]:
                raise ResourceModifiedError()
        self.version += 1
        etag = str(self.version)
        self.blobs[name] = (data, etag)
        return {"etag": etag}

    def get_blob_client(self, name):
        return SimpleNamespace(upload_blob=lambda data, **kwargs: self.upload_blob(name, data, **kwargs))

    def delete_blob(self, name, **kwargs):
        if kwargs["etag"] != self.blobs[name][1]:
            raise ResourceModifiedError()
        del self.blobs[name]

    def close(self):
        pass


def snapshot():
    return {
        "id": str(uuid4()), "config": {"name": "Demo", "topic": "Test", "rounds": 2},
        "created_at": "2026-11-09", "status": "running", "annotations": {},
        "messages": [], "attempts": [{"status": "streaming"}],
    }


def open_storage(container):
    return BlobStorage("https://test.blob.core.windows.net", "debates", container=container)


def test_snapshot_survives_replica_replacement_and_recovers_interrupted_debate():
    container = FakeContainer()
    store = open_storage(container)
    debate = snapshot()
    store.save(debate)
    debate["config"]["topic"] = "unsaved mutation"
    store.close()
    restored = open_storage(container)
    restored.recover("2026-11-10")
    result = restored.get(debate["id"])
    assert result["config"]["topic"] == "Test"
    assert result["status"] == "interrupted"
    assert result["attempts"][0]["status"] == "interrupted"
    assert restored.list()[0]["turns"] == 0
    assert json.loads(next(iter(container.blobs.values()))[0])["status"] == "interrupted"
    restored.delete(debate["id"])
    assert open_storage(container).list() == []


def test_overlapping_replica_cannot_overwrite_or_delete_newer_snapshot():
    container = FakeContainer()
    first = open_storage(container)
    debate = snapshot()
    first.save(debate)
    stale = open_storage(container)
    newer = copy.deepcopy(debate)
    newer["status"] = "completed"
    first.save(newer)
    with pytest.raises(ResourceModifiedError):
        stale.save(debate)
    with pytest.raises(ResourceModifiedError):
        stale.delete(debate["id"])
    assert open_storage(container).get(debate["id"])["status"] == "completed"


def test_failed_write_does_not_commit_local_state():
    container = FakeContainer()
    store = open_storage(container)
    debate = snapshot()
    store.save(debate)
    container.fail_write = True
    debate["status"] = "completed"
    with pytest.raises(OSError):
        store.save(debate)
    assert store.get(debate["id"])["status"] == "running"


def test_duplicate_new_id_and_corrupt_snapshot_fail_closed():
    container = FakeContainer()
    first = open_storage(container)
    other = open_storage(container)
    debate = snapshot()
    first.save(debate)
    with pytest.raises(ResourceExistsError):
        other.save(debate)
    container.blobs["debates/broken.json"] = (b"not json", "etag")
    with pytest.raises(ValueError):
        open_storage(container)


def test_real_sdk_upload_contract_and_conditional_headers_without_network():
    class Response(HttpResponse):
        def __init__(self, request):
            super().__init__(request, None)
            self.status_code = 201
            self.headers = CaseInsensitiveDict({
                "etag": '"saved-etag"', "last-modified": "Mon, 09 Nov 2026 12:00:00 GMT",
                "content-length": "0", "x-ms-request-id": "test",
            })

        def body(self):
            return b""

        def stream_download(self, pipeline, **kwargs):
            return iter([])

    class Transport(HttpTransport):
        def __init__(self):
            self.requests = []

        def open(self):
            pass

        def close(self):
            pass

        def __exit__(self, *args):
            self.close()

        def send(self, request, **kwargs):
            self.requests.append(request)
            return Response(request)

    transport = Transport()
    store = open_storage(FakeContainer())
    store.container = ContainerClient(
        "https://example.blob.core.windows.net", "debates",
        credential=AzureSasCredential("sig=test"), transport=transport,
    )
    debate = snapshot()
    store.save(debate)
    store.save(debate)
    assert transport.requests[0].headers["If-None-Match"] == "*"
    assert transport.requests[1].headers["If-Match"] == '"saved-etag"'
    assert store.get(debate["id"])["status"] == "running"
    store.close()
