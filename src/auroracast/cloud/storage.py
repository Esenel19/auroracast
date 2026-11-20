"""Lecture et écriture des fichiers du projet, sur le disque ou dans Azure Blob Storage.

Sur Azure, l'accès passe par l'identité gérée du job ou de l'API (DefaultAzureCredential) :
aucune clé ni chaîne de connexion. Le compte de stockage refuse d'ailleurs la clé partagée
(garde-fou C5 du TP3). En local, ou dans GitHub Actions après « az login » en OIDC, la même
classe utilise le compte connecté.

    AZURE_STORAGE_ACCOUNT=stauroracasttp3   # nom du compte ; absent = mode local

Conteneurs (tous privés) :

    raw      données brutes, jamais réécrites (un fichier = une collecte horodatée)
    clean    tables propres (Parquet), remplacées à chaque transformation
    models   latest/ (modèles en service) et versions/<horodatage>/ (historique)
    site     forecast.json et suivi_modeles.json, lus par l'API
    rapports rapports datés : qualité des données, modèle
"""

from __future__ import annotations

import os
from pathlib import Path

ACCOUNT_ENV = "AZURE_STORAGE_ACCOUNT"


def account_name() -> str | None:
    return os.environ.get(ACCOUNT_ENV) or None


def _service():
    from azure.identity import DefaultAzureCredential
    from azure.storage.blob import BlobServiceClient

    name = account_name()
    if not name:
        raise RuntimeError(f"{ACCOUNT_ENV} n'est pas défini")
    # exclude_interactive : jamais de fenêtre de connexion dans un job
    cred = DefaultAzureCredential(exclude_interactive_browser_credential=True)
    return BlobServiceClient(f"https://{name}.blob.core.windows.net", credential=cred)


def _join(*parts: str) -> str:
    return "/".join(p.strip("/") for p in parts if p and p.strip("/"))


class Store:
    """Un dossier logique : un répertoire local ou un préfixe dans un conteneur Blob."""

    def read_bytes(self, name: str) -> bytes | None:  # pragma: no cover - interface
        raise NotImplementedError

    def download(self, dest: Path, prefix: str = "") -> int:  # pragma: no cover
        raise NotImplementedError

    def upload(self, src: Path, prefix: str = "", overwrite: bool = True) -> int:  # pragma: no cover
        raise NotImplementedError

    def upload_file(self, path: Path, name: str) -> bool:
        """Envoie un seul fichier sous le nom ``name`` ; False s'il n'existe pas."""
        path = Path(path)
        if not path.is_file():
            return False
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(path.read_bytes())
            return self.upload(Path(tmp)) == 1


class LocalStore(Store):
    def __init__(self, root: Path | str):
        self.root = Path(root)

    def read_bytes(self, name: str) -> bytes | None:
        p = self.root / name
        return p.read_bytes() if p.is_file() else None

    def download(self, dest: Path, prefix: str = "") -> int:
        src = self.root / prefix
        return _copy_tree(src, Path(dest), overwrite=True) if src.is_dir() else 0

    def upload(self, src: Path, prefix: str = "", overwrite: bool = True) -> int:
        return _copy_tree(Path(src), self.root / prefix, overwrite=overwrite)


class BlobStore(Store):
    def __init__(self, container: str, prefix: str = "", service=None):
        self.container = container
        self.prefix = prefix.strip("/")
        self._service = service

    @property
    def client(self):
        if self._service is None:
            self._service = _service()
        return self._service.get_container_client(self.container)

    def read_bytes(self, name: str) -> bytes | None:
        from azure.core.exceptions import ResourceNotFoundError

        try:
            return self.client.download_blob(_join(self.prefix, name)).readall()
        except ResourceNotFoundError:
            return None

    def download(self, dest: Path, prefix: str = "") -> int:
        """Copie tous les blobs sous le préfixe dans ``dest`` (chemins relatifs au préfixe)."""
        base = _join(self.prefix, prefix)
        start = len(base) + 1 if base else 0
        n = 0
        for blob in self.client.list_blobs(name_starts_with=f"{base}/" if base else None):
            target = Path(dest) / blob.name[start:]
            if target.is_file() and target.stat().st_size == blob.size:
                continue                                  # déjà là (relance du job)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(self.client.download_blob(blob.name).readall())
            n += 1
        return n

    def upload(self, src: Path, prefix: str = "", overwrite: bool = True) -> int:
        """Envoie chaque fichier de ``src``. Sans ``overwrite``, un blob existant est gardé."""
        from azure.core.exceptions import ResourceExistsError

        n = 0
        for f in _files(Path(src)):
            name = _join(self.prefix, prefix, f.relative_to(src).as_posix())
            try:
                with f.open("rb") as fh:
                    self.client.upload_blob(name, fh, overwrite=overwrite)
                n += 1
            except ResourceExistsError:
                pass                                      # donnée brute déjà archivée
        return n


def store(container: str, prefix: str = "", local_root: Path | str | None = None) -> Store:
    """Blob si AZURE_STORAGE_ACCOUNT est défini, sinon le dossier local ``local_root``."""
    if account_name():
        return BlobStore(container, prefix)
    return LocalStore(Path(local_root if local_root is not None else container) / prefix)


def _files(root: Path):
    return sorted(p for p in root.rglob("*") if p.is_file()) if root.is_dir() else []


def _copy_tree(src: Path, dest: Path, overwrite: bool) -> int:
    n = 0
    for f in _files(src):
        target = dest / f.relative_to(src)
        if target.exists() and not overwrite:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(f.read_bytes())
        n += 1
    return n
