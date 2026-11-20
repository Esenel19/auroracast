"""Tâches planifiées de la chaîne AuroraCast sur Azure (Container Apps Jobs).

Chaque tâche démarre dans un conteneur vide : elle récupère dans le stockage Blob ce dont
elle a besoin, appelle les mêmes modules que GitHub Actions, puis renvoie ses résultats.

    python -m auroracast.cloud.jobs collect     # toutes les 3 h : NOAA + MET Norway -> raw
    python -m auroracast.cloud.jobs ghcnh       # chaque jour : observations GHCNh -> raw
    python -m auroracast.cloud.jobs transform   # raw -> clean -> PostgreSQL -> prévisions -> site
    python -m auroracast.cloud.jobs train       # PostgreSQL -> modèles (latest + versions) + rapport
    python -m auroracast.cloud.jobs tune        # chaque semaine : algorithmes, variables, réglages -> rapports

Variables : AZURE_STORAGE_ACCOUNT (stockage), DATABASE_URL (lu dans Key Vault par
Container Apps), AURORACAST_CONTACT (User-Agent des collectes). Sans AZURE_STORAGE_ACCOUNT,
tout se passe dans le dossier AURORACAST_LOCAL_STORE (par défaut data/azure-local) : la chaîne
se rejoue sur le PC sans compte Azure.

Code de sortie : 0 si tout a réussi, 1 sinon. Container Apps marque alors l'exécution en
échec et l'alerte Azure Monitor prévient par e-mail.
"""

from __future__ import annotations

import argparse
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from auroracast.cloud.storage import store

LOCAL_ROOT_ENV = "AURORACAST_LOCAL_STORE"


def _root() -> Path:
    return Path(os.environ.get(LOCAL_ROOT_ENV, "data/azure-local"))


def _s(container: str, prefix: str = ""):
    return store(container, prefix, local_root=_root() / container)


def _stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%MZ")


def _log(msg: str) -> None:
    print(f"[{datetime.now(UTC):%Y-%m-%dT%H:%M:%SZ}] {msg}", flush=True)


def _run(label: str, fn, argv: list[str]) -> bool:
    """Lance un module ; une exception ou un code non nul = échec, sans arrêter la tâche."""
    _log(f"{label} : début")
    try:
        code = fn(argv)
    except SystemExit as e:                       # argparse.error, raise SystemExit(...)
        code = e.code if isinstance(e.code, int) else 1
    except Exception as e:                         # noqa: BLE001 - on archive ce qui a réussi
        _log(f"{label} : erreur {type(e).__name__} : {e}")
        code = 1
    _log(f"{label} : {'ok' if not code else f'échec (code {code})'}")
    return not code


def collect(work: Path) -> bool:
    from auroracast.collect import metno, noaa

    raw, state = work / "raw", work / "state"
    raw.mkdir(parents=True, exist_ok=True)
    for prefix in ("noaa/manifests", "metno/manifests"):
        n = _s("raw").download(state / prefix, prefix)
        _log(f"état : {n} manifestes {prefix}")
    ok = _run("collecte NOAA", noaa.main,
              ["--out", str(raw), "--run-manifest", "--state-dir", str(state)])
    ok &= _run("prévisions MET Norway", metno.main, ["--out", str(raw), "--state-dir", str(state)])
    _log(f"archivage : {_s('raw').upload(raw, overwrite=False)} fichiers bruts")
    return ok


def ghcnh(work: Path) -> bool:
    from auroracast.collect import ghcnh as gh

    raw, state = work / "raw", work / "state"
    raw.mkdir(parents=True, exist_ok=True)
    _s("raw").download(state / "ghcnh/manifests", "ghcnh/manifests")
    ok = _run("mise à jour GHCNh", gh.main, ["update", "--out", str(raw), "--state-dir", str(state)])
    _log(f"archivage : {_s('raw').upload(raw, overwrite=False)} fichiers bruts")
    return ok


def transform(work: Path) -> bool:
    from auroracast.app import analysis, predict
    from auroracast.model import history
    from auroracast.transform import __main__ as tr
    from auroracast.warehouse import load

    raw, clean, site = work / "raw", work / "clean", work / "site"
    _log(f"données brutes : {_s('raw').download(raw)} fichiers téléchargés")
    if not _run("tables propres", tr.main, ["--raw", str(raw), "--out", str(clean)]):
        return False
    _log(f"tables propres : {_s('clean').upload(clean)} fichiers publiés")
    _s("rapports").upload_file(clean / "rapport_qualite.json", f"qualite/{_stamp()}/rapport_qualite.json")
    if not os.environ.get("DATABASE_URL"):
        _log("DATABASE_URL absent : entrepôt, prévisions et suivi sautés")
        return True

    ok = _run("chargement de l'entrepôt", load.main, ["--clean", str(clean)])
    models, versions = work / "models", work / "versions"
    n = _s("models").download(models, "latest")
    _s("models").download(versions, "versions")
    if n:
        if versions.is_dir():
            ok &= _run("suivi des modèles", history.main,
                       ["suivi", "--versions-dir", str(versions), "--models", str(models),
                        "--out", str(site)])
        ok &= _run("prévisions des 48 h", predict.main, ["--out", str(site), "--models", str(models)])
    else:
        _log("aucun modèle dans le conteneur models : lancer d'abord la tâche train")
        ok = False
    ok &= _run("analyse saisonnière", analysis.main, ["--out", str(site)])
    if site.is_dir():
        _log(f"site : {_s('site').upload(site)} fichiers publiés")
    return ok


def train(work: Path) -> bool:
    from auroracast.model import history
    from auroracast.model import train as tm

    out = work / "trained"
    if not _run("entraînement", tm.main, ["--out", str(out)]):
        return False                               # contrôle qualité raté : l'ancien modèle reste
    _run("progression rejouée", history.main, ["backtest", "--out", str(out)])
    stamp = _stamp()
    _s("models").upload(out, f"versions/{stamp}")
    _s("models").upload(out, "latest")
    _s("rapports").upload_file(out / "rapport_modele.md", f"modele/{stamp}/rapport_modele.md")
    _log(f"modèles publiés : latest et versions/{stamp}")
    return True


def tune(work: Path) -> bool:
    """Optimisation : rapport et réglage proposé dans rapports/optimisation/<horodatage>/.

    Rien n'est mis en production ici : le réglage proposé est adopté dans config.json après lecture.
    """
    from auroracast.model import tune as tn

    out = work / "optimisation"
    if not _run("optimisation", tn.main, ["--out", str(out)]):
        return False
    stamp = _stamp()
    n = _s("rapports").upload(out, f"optimisation/{stamp}")
    _log(f"rapport d'optimisation publié : rapports/optimisation/{stamp} ({n} fichiers)")
    return True


TASKS = {"collect": collect, "ghcnh": ghcnh, "transform": transform, "train": train, "tune": tune}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("task", choices=sorted(TASKS))
    parser.add_argument("--work", type=Path, help="dossier de travail (par défaut : temporaire)")
    args = parser.parse_args(argv)

    from dotenv import load_dotenv
    load_dotenv()
    _log(f"tâche {args.task} ; stockage : "
         f"{os.environ.get('AZURE_STORAGE_ACCOUNT') or f'local ({_root()})'}")
    if args.work:
        args.work.mkdir(parents=True, exist_ok=True)
        ok = TASKS[args.task](args.work)
    else:
        with tempfile.TemporaryDirectory(prefix="auroracast-") as tmp:
            ok = TASKS[args.task](Path(tmp))
    _log(f"tâche {args.task} : {'réussie' if ok else 'en échec'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
