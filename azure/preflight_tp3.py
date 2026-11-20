"""AuroraCast · TP3 · Simulateur de prévol local (aucun appel au fournisseur).

Usage, depuis la racine du dépôt :

    python azure/preflight_tp3.py . azure/plan_tp3.json azure/deploiement_azure.sh azure/rapport_prevol.json

Vérifie localement ce qui peut l'être : le dépôt, le plan, le script de déploiement et le modèle
Bicep (azure/main.bicep, à côté du script). Liste aussi ce qui reste à prouver dans la console
Azure. Une simulation locale NE PROUVE PAS que le cloud est provisionné. Sans les preuves P1 à P6
(fichier azure/preuves_console.json, rempli à la main après la démonstration), la décision reste
NO-GO pour le cloud réel.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

SECRET_PATTERNS = {
    "cle_stockage_azure": r"AccountKey=[A-Za-z0-9+/=]{20,}",
    "chaine_connexion_azure": r"DefaultEndpointsProtocol=https;AccountName=\w+;AccountKey=",
    "cle_privee": r"-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----",
    "cle_compte_service_gcp": r"\"private_key\"\s*:\s*\"-----BEGIN",
    "jeton_github": r"gh[pousr]_[A-Za-z0-9]{30,}",
    "cle_aws": r"AKIA[0-9A-Z]{16}",
    "url_postgres_avec_mot_de_passe": r"postgres(?:ql)?://[^:\s/]+:[^@\s]{6,}@(?!localhost|127\.0\.0\.1)[^\s'\"]+",
}
TEXT_EXT = {".py", ".yml", ".yaml", ".json", ".md", ".sql", ".sh", ".bat", ".toml", ".txt", ".html", ".ipynb",
            ".env", ".example", ".cfg", ".ini", ".bicep"}
# Rôles larges interdits (identifiants des rôles intégrés Owner, Contributor, User Access Administrator)
BROAD_ROLES = {"8e3af657-a8ff-443c-a75c-2fe8c4bcb635": "Owner", "b24988ac-6180-42a0-ab88-20f7382dd24c": "Contributor",
               "18d7d88d-d35e-4fb5-a5c3-7773c20a72d9": "User Access Administrator"}
CONSOLE = [
    {"id": "P1", "a_prouver": "Abonnement « Azure for Students » actif, sans moyen de paiement",
     "preuve_attendue": "capture Abonnements (nom de l'offre, état), identifiant masqué"},
    {"id": "P2", "a_prouver": "Crédit restant et date d'expiration",
     "preuve_attendue": "capture du solde de crédit (Microsoft Azure Sponsorships / portail), chaque mois"},
    {"id": "P3", "a_prouver": "Régions autorisées par la politique de l'abonnement (francecentral)",
     "preuve_attendue": "capture Policy > Allowed locations (aussi listée par le script, étape 0b)"},
    {"id": "P4", "a_prouver": "Suivi des coûts : budget indisponible pour l'offre, relevé du crédit à la place",
     "preuve_attendue": "capture du message « offre non prise en charge » de Cost Management + relevé P2 mensuel"},
    {"id": "P5", "a_prouver": "Ressources réellement créées et privées",
     "preuve_attendue": "az resource list -g rg-auroracast-tp3 ; allowBlobPublicAccess=false, allowSharedKeyAccess=false"},
    {"id": "P6", "a_prouver": "Suppression effective (testée sur rg-auroracast-test, puis en fin de projet)",
     "preuve_attendue": "az group show -n rg-auroracast-test → ResourceGroupNotFound"},
]


def check(id_, libelle, ok, preuve, role):
    return {"id": id_, "controle": libelle, "statut": "ok" if ok else "echec", "preuve": preuve, "responsable": role}


def tracked_files(repo: Path) -> list[Path]:
    out = subprocess.run(["git", "-C", str(repo), "ls-files"], capture_output=True, text=True, check=True).stdout
    return [repo / f for f in out.splitlines()]


def scan_secrets(repo: Path) -> list[dict]:
    found = []
    for f in tracked_files(repo):
        if f.suffix not in TEXT_EXT and f.name not in {".env.example", ".gitignore"}:
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for name, pat in SECRET_PATTERNS.items():
            for m in re.finditer(pat, text):
                found.append({"fichier": str(f.relative_to(repo)), "type": name,
                              "ligne": text.count("\n", 0, m.start()) + 1})
    return found


def bicep_checks(bicep: str, plan: dict) -> dict:
    """Le modèle Bicep applique-t-il vraiment le plan ? (lecture du texte, sans compilation)"""
    st = next(r for r in plan["ressources"] if r["type"] == "stockage")
    names = re.search(r"var containerNames = \[([^\]]*)\]", bicep)
    bicep_containers = re.findall(r"'([a-z]+)'", names.group(1)) if names else []
    timeout = re.search(r"param jobTimeout int = (\d+)", bicep)
    return {
        "conteneurs_identiques_au_plan": sorted(bicep_containers) == sorted(st["conteneurs"]),
        "acces_public_blob_desactive": "allowBlobPublicAccess: false" in bicep,
        "cle_partagee_desactivee": "allowSharedKeyAccess: false" in bicep,
        "tls_1_2": "minimumTlsVersion: 'TLS1_2'" in bicep,
        "journaux_30_j_0_1_go": "retentionInDays: 30" in bicep and "dailyQuotaGb: json('0.1')" in bicep,
        "taches_900_s_au_plus": bool(timeout) and int(timeout.group(1)) <= 900,
        "aucune_relance_automatique": "replicaRetryLimit: 0" in bicep,
        "aucun_role_large": not any(r in bicep for r in BROAD_ROLES),
        "ingress_https_seulement": "allowInsecure: false" in bicep,
        "federation_github_branche_main": ":ref:refs/heads/main" in bicep,
    }


def main(argv):
    repo, plan_path, script_path = Path(argv[1]), Path(argv[2]), Path(argv[3])
    out = Path(argv[4]) if len(argv) > 4 else Path("rapport_prevol.json")
    bicep_path = script_path.parent / "main.bicep"
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    script = script_path.read_text(encoding="utf-8")
    bicep = bicep_path.read_text(encoding="utf-8") if bicep_path.exists() else ""
    checks = []

    # --- Dépôt
    secrets = scan_secrets(repo)
    checks.append(check("C1", "Aucune clé ni secret dans les fichiers suivis du dépôt", not secrets,
                        secrets or f"{len(tracked_files(repo))} fichiers analysés, 0 motif trouvé", "partagé"))
    gi = (repo / ".gitignore").read_text(encoding="utf-8") if (repo / ".gitignore").exists() else ""
    checks.append(check("C2", "Le fichier .env est exclu de Git", bool(re.search(r"^\.env$", gi, re.MULTILINE)),
                        ".gitignore contient '.env'" if ".env" in gi else ".gitignore sans .env", "partagé"))
    wf = sorted((repo / ".github" / "workflows").glob("*.yml"))
    bad_ref = [w.name for w in wf if re.search(r"(PASSWORD|SECRET|KEY|TOKEN)\s*:\s*['\"]?[A-Za-z0-9+/]{16,}",
                                                  w.read_text(encoding="utf-8"))]
    checks.append(check("C3", "Les workflows lisent les secrets par référence (${{ secrets.X }})", not bad_ref,
                        bad_ref or [w.name for w in wf], "partagé"))
    no_timeout = []
    for w in wf:
        t = w.read_text(encoding="utf-8")
        jobs = len(re.findall(r"^\s{2}[a-z_-]+:\s*$\n(?:\s{4}.*\n)*?\s{4}runs-on:", t, re.MULTILINE))
        if jobs and t.count("timeout-minutes") < jobs and "deploy-pages" not in t:
            no_timeout.append(w.name)
    checks.append(check("C4", "Durée maximale fixée sur les jobs (coût et blocage bornés)", not no_timeout,
                        no_timeout or "timeout-minutes présent", "partagé"))

    # --- Plan
    st = next((r for r in plan["ressources"] if r["type"] == "stockage"), {})
    checks.append(check("C5", "Stockage privé : accès public désactivé, TLS 1.2, pas de clé partagée",
                        st.get("acces_public_blob") is False and st.get("tls_min") == "TLS1_2"
                        and st.get("acces_par_cle_partagee") is False, st, "B4"))
    ex = [r for r in plan["ressources"] if r["type"] == "execution"]
    ex_ok = bool(ex) and all(r.get("gpu") is False and r.get("duree_max_s", 1e9) <= 900
                             and r.get("declenchement") in {"manuel", "planifie"} and r.get("relances") == 0
                             for r in ex)
    checks.append(check("C6", "Calcul CPU seul, 900 s au plus, sans relance, déclenchement maîtrisé (manuel ou planifié)",
                        ex_ok, [{k: r.get(k) for k in ("nom_logique", "cpu", "gpu", "duree_max_s", "declenchement",
                                                       "frequence", "relances")} for r in ex], "B5"))
    ids = [r for r in plan["ressources"] if r["type"].startswith("identite")]
    ids_ok = bool(ids) and all(r.get("role") not in {"Owner", "Contributor"}
                               and "abonnement" not in r.get("portee", "") for r in ids)
    checks.append(check("C7", "Une identité par usage, rôles précis, portée limitée (ni Owner ni Contributor, jamais l'abonnement)",
                        ids_ok, [{k: r.get(k) for k in ("nom_logique", "role", "portee")} for r in ids], "partagé"))
    lg = next((r for r in plan["ressources"] if r["type"] == "journaux"), {})
    checks.append(check("C8", "Journaux : rétention et plafond quotidien fixés",
                        lg.get("retention_jours", 999) <= 30 and lg.get("plafond_quotidien_go", 99) <= 0.5, lg, "partagé"))
    b = plan["budget"]
    budget_ok = (b.get("disponible") and b.get("montant_eur", 99) <= 10 and 100 in b.get("seuils_alerte_pct", [])) or \
                (b.get("disponible") is False and bool(b.get("remplacement")))
    checks.append(check("C9", "Suivi des coûts (budget, ou relevé du crédit si l'offre n'a pas de budget), "
                        "sans le confondre avec un arrêt automatique", budget_ok and b["arret_automatique"] is False, b, "partagé"))
    expire = date.fromisoformat(plan["tags"]["expire-le"])
    checks.append(check("C10", "Date de fin étiquetée, procédure de suppression écrite et testée",
                        expire <= date.today().replace(year=date.today().year + 1)
                        and "az group delete" in script and "rg-auroracast-test" in script,
                        {"expire-le": plan["tags"]["expire-le"], "test": "rg-auroracast-test créé puis supprimé"}, "partagé"))
    checks.append(check("C11", "Offre sans carte et sans passage au payant",
                        plan["carte_bancaire"] is False and plan["passage_payant_autorise"] is False,
                        {k: plan[k] for k in ("offre", "carte_bancaire", "comportement_fin_credit")}, "partagé"))
    checks.append(check("C12", "Région, service et limites documentés", bool(plan.get("region")) and bool(plan.get("region_justification")),
                        {"region": plan["region"], "justification": plan["region_justification"]}, "partagé"))
    checks.append(check("C13", "Script de déploiement : simulation par défaut, confirmation explicite, aucun secret écrit ni affiché",
                        'DRY_RUN="${DRY_RUN:-1}"' in script and 'read -r -p' in script and '"OUI"' in script
                        and 'read -r -s' in script and not re.search(SECRET_PATTERNS["cle_stockage_azure"], script),
                        "simulation par défaut ; confirmation OUI avant création ; secret saisi sans écho", "partagé"))
    bc = bicep_checks(bicep, plan) if bicep else {"main.bicep": "absent"}
    checks.append(check("C14", "Le modèle Bicep applique le plan (conteneurs, stockage privé, journaux, 900 s, rôles, HTTPS)",
                        bool(bicep) and all(bc.values()), bc, "partagé"))

    # --- Preuves console (remplies à la main après la démonstration)
    proofs_file = plan_path.parent / "preuves_console.json"
    proofs = json.loads(proofs_file.read_text(encoding="utf-8")) if proofs_file.exists() else {}
    console = [{**p, "fournie": bool(proofs.get(p["id"]))} for p in CONSOLE]
    local_ok = all(c["statut"] == "ok" for c in checks)
    proofs_ok = all(p["fournie"] for p in console)
    n_ok = sum(c["statut"] == "ok" for c in checks)
    go = local_ok and proofs_ok
    rapport = {
        "date": date.today().isoformat(),
        "outil": "preflight_tp3.py (simulation locale, aucun appel Azure)",
        "depot": "github.com/Esenel19/auroracast",
        "controles_locaux": checks,
        "controles_locaux_ok": f"{n_ok}/{len(checks)}",
        "a_verifier_console": console,
        "decision": "GO cloud réel" if go else "NO-GO cloud réel ; démonstration locale",
        "ecarts_locaux": [c["id"] for c in checks if c["statut"] != "ok"],
        "justification": (f"{n_ok} contrôles locaux sur {len(checks)} au vert ; "
                          f"preuves fournisseur : {sum(p['fournie'] for p in console)}/{len(console)}. "
                          + ("Toutes les conditions sont prouvées." if go else
                             "Condition incertaine = NO-GO pour le cloud réel ; la démonstration reste locale.")
                          + ("" if local_ok else " Écarts locaux à corriger avant tout GO : "
                             + ", ".join(c["id"] for c in checks if c["statut"] != "ok") + ".")),
    }
    out.write_text(json.dumps(rapport, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for c in checks:
        print(f"[{c['statut']:5}] {c['id']:3} {c['controle']}")
    print(f"\nPreuves console : {sum(p['fournie'] for p in console)}/{len(console)}")
    print(f"Décision : {rapport['decision']}")
    return 0 if local_ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
