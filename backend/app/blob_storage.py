"""Durable snapshots for the single-instance college deployment."""

import copy
import json
import os
from uuid import UUID

from azure.core import MatchConditions
from azure.identity import ManagedIdentityCredential
from azure.storage.blob import ContainerClient, ContentSettings

from .storage import Storage


class BlobStorage(Storage):
    # Reuse Storage.recover(), keeping local SQLite unchanged.
    def __init__(self, account_url: str, container_name: str, *, container=None):
        self.credential = None
        if container is None:
            if not account_url.startswith("https://") or "?" in account_url:
                raise ValueError("AZURE_STORAGE_ACCOUNT_URL must be an HTTPS account URL")
            self.credential = ManagedIdentityCredential(client_id=os.getenv("AZURE_CLIENT_ID"))
            container = ContainerClient(
                account_url, container_name, credential=self.credential,
                connection_timeout=5, read_timeout=15, retry_total=2,
            )
        self.container = container
        self.records = {}
        self.etags = {}
        # Missing container/access errors must fail startup, never silently start empty.
        for blob in container.list_blobs(name_starts_with="debates/"):
            if not blob.name.endswith(".json"):
                continue
            download = container.download_blob(blob.name)
            debate = json.loads(download.readall())
            debate_id = debate["id"]
            if self.blob_name(debate_id) != blob.name:
                raise ValueError("Invalid debate snapshot name")
            self.records[debate_id] = debate
            self.etags[debate_id] = download.properties.etag

    @staticmethod
    def blob_name(debate_id):
        if str(UUID(debate_id)) != debate_id:
            raise ValueError("Invalid debate ID")
        return f"debates/{debate_id}.json"

    def save(self, debate: dict) -> None:
        debate_id = debate["id"]
        options = {"overwrite": False}
        if debate_id in self.etags:
            options = {
                "overwrite": True, "etag": self.etags[debate_id],
                "match_condition": MatchConditions.IfNotModified,
            }
        result = self.container.get_blob_client(self.blob_name(debate_id)).upload_blob(
            json.dumps(debate, ensure_ascii=False).encode(),
            content_settings=ContentSettings(content_type="application/json"), **options,
        )
        # Commit local state only after durable storage acknowledges the write.
        self.etags[debate_id] = result["etag"]
        self.records[debate_id] = copy.deepcopy(debate)

    def get(self, debate_id: str) -> dict:
        return copy.deepcopy(self.records[debate_id])

    def delete(self, debate_id: str) -> None:
        if debate_id not in self.records:
            raise KeyError(debate_id)
        self.container.delete_blob(
            self.blob_name(debate_id), etag=self.etags[debate_id],
            match_condition=MatchConditions.IfNotModified,
        )
        del self.records[debate_id]
        del self.etags[debate_id]

    def list(self) -> list[dict]:
        return [
            {
                "id": item["id"], "name": item["config"].get("name"),
                "topic": item["config"]["topic"], "created_at": item["created_at"],
                "status": item["status"], "outcome": item["annotations"].get("outcome"),
                "turns": len(item["messages"]), "rounds": item["config"]["rounds"],
            }
            for item in sorted(
                self.records.values(), key=lambda item: item["created_at"], reverse=True
            )
        ]

    def close(self) -> None:
        self.container.close()
        if self.credential:
            self.credential.close()
